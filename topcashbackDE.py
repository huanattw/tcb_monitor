import os
import logging
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

from cashback_clients import create_client
from monitoring_config import MARKET_CONFIG

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3600
HISTORY_LIMIT = 200
PORT = 5001
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()


class MonitorStore:
    def __init__(
        self,
        market_code,
        market_name,
        poll_interval_seconds=30,
        history_limit=200,
        db_path=None,
    ):
        self.market_code = market_code
        self.market_name = market_name
        self.poll_interval_seconds = poll_interval_seconds
        self.history_limit = history_limit
        self.db_path = db_path
        self.lock = threading.Lock()
        self.results = []
        self.last_checked_utc = None
        self.history_by_merchant = {}
        self.highest_by_merchant = {}
        self._init_db()
        self._load_history()

    @staticmethod
    def _parse_rate_value(rate_text):
        if not isinstance(rate_text, str):
            return None

        match = re.search(r"(\d+(?:[\.,]\d+)?)", rate_text)
        if not match:
            return None

        normalized = match.group(1).replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None

    @staticmethod
    def _to_local_time_str(iso_time_text):
        if not iso_time_text:
            return None

        try:
            dt = datetime.fromisoformat(iso_time_text)
        except ValueError:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _db_connect(self):
        if not self.db_path:
            return None
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._db_connect()
        if conn is None:
            return

        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_code TEXT NOT NULL,
                    merchant TEXT NOT NULL,
                    checked_at_utc TEXT,
                    rate TEXT,
                    rate_value REAL,
                    aff TEXT,
                    error TEXT,
                    url TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_history_market_merchant_time
                ON history (market_code, merchant, checked_at_utc)
                """
            )

            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(history)").fetchall()
            }
            if "aff" not in columns:
                conn.execute("ALTER TABLE history ADD COLUMN aff TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS highest (
                    market_code TEXT NOT NULL,
                    merchant TEXT NOT NULL,
                    rate TEXT,
                    rate_value REAL,
                    checked_at_utc TEXT,
                    PRIMARY KEY (market_code, merchant)
                )
                """
            )

        conn.close()

    def _load_history(self):
        conn = self._db_connect()
        if conn is None:
            return

        self.history_by_merchant = {}
        self.highest_by_merchant = {}

        rows = conn.execute(
            """
            SELECT merchant, checked_at_utc, rate, aff, error, url
            FROM history
            WHERE market_code = ?
            ORDER BY checked_at_utc ASC, id ASC
            """,
            (self.market_code,),
        ).fetchall()

        for row in rows:
            history_list = self.history_by_merchant.setdefault(row["merchant"], [])
            history_list.append(
                {
                    "checked_at_utc": row["checked_at_utc"],
                    "rate": row["rate"],
                    "aff": row["aff"],
                    "error": row["error"],
                    "url": row["url"],
                }
            )
            if len(history_list) > self.history_limit:
                del history_list[: len(history_list) - self.history_limit]

        high_rows = conn.execute(
            """
            SELECT merchant, rate, rate_value, checked_at_utc
            FROM highest
            WHERE market_code = ?
            """,
            (self.market_code,),
        ).fetchall()

        for row in high_rows:
            self.highest_by_merchant[row["merchant"]] = {
                "rate": row["rate"],
                "rate_value": row["rate_value"],
                "checked_at_utc": row["checked_at_utc"],
            }

        conn.close()

    def _save_rows_to_db(self, results):
        conn = self._db_connect()
        if conn is None:
            return

        with conn:
            for item in results:
                merchant = item.get("merchant")
                if not merchant:
                    continue

                rate_value = self._parse_rate_value(item.get("rate"))
                conn.execute(
                    """
                    INSERT INTO history (
                        market_code, merchant, checked_at_utc, rate, rate_value, aff, error, url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.market_code,
                        merchant,
                        item.get("checked_at_utc"),
                        item.get("rate"),
                        rate_value,
                        item.get("aff"),
                        item.get("error"),
                        item.get("url"),
                    ),
                )

                if item.get("error") is None and rate_value is not None:
                    current = conn.execute(
                        """
                        SELECT rate_value
                        FROM highest
                        WHERE market_code = ? AND merchant = ?
                        """,
                        (self.market_code, merchant),
                    ).fetchone()
                    if current is None or rate_value > current["rate_value"]:
                        conn.execute(
                            """
                            INSERT INTO highest (
                                market_code, merchant, rate, rate_value, checked_at_utc
                            ) VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(market_code, merchant)
                            DO UPDATE SET
                                rate = excluded.rate,
                                rate_value = excluded.rate_value,
                                checked_at_utc = excluded.checked_at_utc
                            """,
                            (
                                self.market_code,
                                merchant,
                                item.get("rate"),
                                rate_value,
                                item.get("checked_at_utc"),
                            ),
                        )

        conn.close()

    def update(self, results):
        with self.lock:
            self.results = results
            self.last_checked_utc = datetime.now(timezone.utc)

            for item in results:
                merchant = item.get("merchant")
                if not merchant:
                    continue

                history_list = self.history_by_merchant.setdefault(merchant, [])
                history_list.append(
                    {
                        "checked_at_utc": item.get("checked_at_utc"),
                        "rate": item.get("rate"),
                        "aff": item.get("aff"),
                        "error": item.get("error"),
                        "url": item.get("url"),
                    }
                )
                if len(history_list) > self.history_limit:
                    del history_list[: len(history_list) - self.history_limit]

                rate_value = self._parse_rate_value(item.get("rate"))
                if item.get("error") is None and rate_value is not None:
                    current_high = self.highest_by_merchant.get(merchant)
                    if current_high is None or rate_value > current_high.get(
                        "rate_value", float("-inf")
                    ):
                        self.highest_by_merchant[merchant] = {
                            "rate": item.get("rate"),
                            "rate_value": rate_value,
                            "checked_at_utc": item.get("checked_at_utc"),
                        }

            self._save_rows_to_db(results)

    def find_rate_changes(self, results):
        changes = []
        with self.lock:
            for item in results:
                if item.get("error") is not None:
                    continue

                new_value = self._parse_rate_value(item.get("rate"))
                if new_value is None:
                    continue

                merchant = item.get("merchant")
                previous_rate = None
                previous_value = None
                for record in reversed(self.history_by_merchant.get(merchant, [])):
                    value = self._parse_rate_value(record.get("rate"))
                    if record.get("error") is None and value is not None:
                        previous_rate = record.get("rate")
                        previous_value = value
                        break

                if (
                    previous_value is None
                    or new_value <= previous_value
                    or new_value < 100
                ):
                    continue

                changes.append(
                    {
                        "merchant": merchant,
                        "previous_rate": previous_rate,
                        "new_rate": item.get("rate"),
                    }
                )
        return changes

    def snapshot(self):
        with self.lock:
            last_checked_local = (
                self.last_checked_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if self.last_checked_utc
                else None
            )

            enriched_results = []
            for item in self.results:
                merchant = item.get("merchant")
                merchant_history = self.history_by_merchant.get(merchant, [])
                high_data = self.highest_by_merchant.get(merchant)
                aff_high_value = None
                aff_high_checked_at_utc = None

                for record in merchant_history:
                    value = self._parse_rate_value(record.get("aff"))
                    if value is None:
                        continue

                    if aff_high_value is None or value >= aff_high_value:
                        aff_high_value = value
                        aff_high_checked_at_utc = record.get("checked_at_utc")

                enriched = dict(item)
                enriched["history_count"] = len(merchant_history)
                enriched["last_high_rate"] = (
                    high_data.get("rate") if high_data else None
                )
                enriched["last_high_checked_at_local"] = self._to_local_time_str(
                    high_data.get("checked_at_utc") if high_data else None
                )
                enriched["history_points"] = [
                    value
                    for value in (
                        self._parse_rate_value(record.get("rate"))
                        for record in merchant_history[-20:]
                    )
                    if value is not None
                ]
                enriched["aff_history_points"] = [
                    value
                    for value in (
                        self._parse_rate_value(record.get("aff"))
                        for record in merchant_history[-20:]
                    )
                    if value is not None
                ]
                enriched["aff_last_high_value"] = aff_high_value
                enriched["aff_last_high_checked_at_local"] = self._to_local_time_str(
                    aff_high_checked_at_utc
                )
                enriched_results.append(enriched)

            return {
                "market": self.market_code,
                "market_name": self.market_name,
                "last_checked_local": last_checked_local,
                "poll_interval_seconds": self.poll_interval_seconds,
                "results": enriched_results,
            }

    def history_snapshot(self, merchant_name=None, limit=50):
        with self.lock:
            if merchant_name:
                records = self.history_by_merchant.get(merchant_name, [])
                return {
                    "market": self.market_code,
                    "merchant": merchant_name,
                    "history": records[-limit:],
                }

            summary = {}
            for merchant, records in self.history_by_merchant.items():
                summary[merchant] = {
                    "history_count": len(records),
                    "last_record": records[-1] if records else None,
                    "last_high": self.highest_by_merchant.get(merchant),
                }

            return {
                "market": self.market_code,
                "summary": summary,
            }


def poll_once(client, merchants):
    checked_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for merchant_config in merchants:
        if isinstance(merchant_config, str):
            merchant_config = {"slug": merchant_config}
        merchant_slug = merchant_config["slug"]
        merchant_name = merchant_config.get("name", merchant_slug)
        url = client.build_url(merchant_slug)
        try:
            merchant_data = client.get_merchant_data(merchant_slug)
            rows.append(
                {
                    "merchant": merchant_name,
                    "rate": merchant_data.get("rate"),
                    "aff": merchant_data.get("aff"),
                    "error": None,
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
        except urllib.error.URLError as err:
            logger.warning("Fetch failed merchant=%s error=%s", merchant_name, err.reason)
            rows.append(
                {
                    "merchant": merchant_name,
                    "rate": "N/A",
                    "aff": None,
                    "error": str(err.reason),
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
        except Exception as err:
            logger.exception("Unexpected fetch error merchant=%s", merchant_name)
            rows.append(
                {
                    "merchant": merchant_name,
                    "rate": "N/A",
                    "aff": None,
                    "error": str(err),
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
    return rows


def send_telegram_rate_changes(market_name, changes):
    if not changes:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "Telegram notification skipped market=%s reason=not_configured",
            market_name,
        )
        return

    lines = [f"📈 {market_name} 回饋率變動"]
    for change in changes:
        merchant = escape(str(change["merchant"]))
        previous_rate = escape(str(change["previous_rate"]))
        new_rate = escape(str(change["new_rate"]))
        lines.append(
            f"\n{merchant}  {previous_rate} → <b>{new_rate}</b>"
        )

    payload = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request_data = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_data, timeout=20):
            logger.info(
                "Telegram notification sent market=%s changes=%d",
                market_name,
                len(changes),
            )
    except Exception as err:
        logger.exception(
            "Telegram notification failed market=%s error=%s",
            market_name,
            err,
        )


def build_app():
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    history_limit = HISTORY_LIMIT
    monitors = {
        code: MonitorStore(
            market_code=code,
            market_name=config["name"],
            poll_interval_seconds=poll_interval_seconds,
            history_limit=history_limit,
            db_path=os.path.join(os.path.dirname(__file__), "history.db"),
        )
        for code, config in MARKET_CONFIG.items()
    }
    clients = {code: create_client(config) for code, config in MARKET_CONFIG.items()}

    def worker(market_code):
        monitor = monitors[market_code]
        client = clients[market_code]
        merchants = MARKET_CONFIG[market_code]["merchants"]
        logger.info(
            "Monitor worker started market=%s merchants=%d interval_seconds=%d",
            market_code,
            len(merchants),
            monitor.poll_interval_seconds,
        )
        while True:
            started_at = time.monotonic()
            logger.info("Polling started market=%s", market_code)
            results = poll_once(client, merchants)
            changes = monitor.find_rate_changes(results)
            monitor.update(results)
            failures = sum(1 for item in results if item.get("error") is not None)
            logger.info(
                "Polling completed market=%s results=%d failures=%d changes=%d duration_seconds=%.2f",
                market_code,
                len(results),
                failures,
                len(changes),
                time.monotonic() - started_at,
            )
            send_telegram_rate_changes(monitor.market_name, changes)
            time.sleep(monitor.poll_interval_seconds)

    for code in MARKET_CONFIG:
        threading.Thread(
            target=worker,
            args=(code,),
            daemon=True,
            name=f"monitor-{code}",
        ).start()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SESSION_SECRET or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
    )
    if not SESSION_SECRET:
        logger.warning(
            "SESSION_SECRET is not configured; browser sessions will reset on restart"
        )
    logger.info(
        "Application initialized markets=%d telegram_configured=%s",
        len(MARKET_CONFIG),
        bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
    )

    @app.before_request
    def require_web_session():
        if not request.path.startswith("/api/"):
            return None

        if not session.get("web_access"):
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @app.get("/")
    def index():
        session["web_access"] = True
        markets = [
            {
                "code": code,
                "name": config["name"],
                "provider": config.get("provider", "topcashback"),
                "currency": config.get("currency", ""),
            }
            for code, config in MARKET_CONFIG.items()
        ]
        return render_template("index.html", markets=markets)

    @app.get("/api/status")
    def status():
        return jsonify(
            {
                "poll_interval_seconds": poll_interval_seconds,
                "market_config": {
                    code: {
                        "name": config["name"],
                        "provider": config.get("provider", "topcashback"),
                        "currency": config.get("currency", ""),
                        "supports_aff": config.get("supports_aff", True),
                    }
                    for code, config in MARKET_CONFIG.items()
                },
                "markets": {
                    code: monitor.snapshot() for code, monitor in monitors.items()
                },
            }
        )

    @app.get("/api/status/<market_code>")
    def status_by_market(market_code):
        monitor = monitors.get(market_code.lower())
        if not monitor:
            return jsonify({"error": "Unknown market code"}), 404
        return jsonify(monitor.snapshot())

    @app.get("/api/history")
    def history_all_markets():
        return jsonify(
            {
                "markets": {
                    code: monitor.history_snapshot()
                    for code, monitor in monitors.items()
                }
            }
        )

    @app.get("/api/history/<market_code>/<merchant_name>")
    def history_by_merchant(market_code, merchant_name):
        monitor = monitors.get(market_code.lower())
        if not monitor:
            return jsonify({"error": "Unknown market code"}), 404

        limit = request.args.get("limit", default=50, type=int)
        if limit <= 0:
            limit = 50

        return jsonify(
            monitor.history_snapshot(merchant_name=merchant_name, limit=limit)
        )

    return app


app = build_app()


if __name__ == "__main__":
    logger.info("Starting web server host=0.0.0.0 port=%d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
