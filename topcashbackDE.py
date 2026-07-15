import os
import importlib
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from html import unescape
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request


POLL_INTERVAL_SECONDS = 3600
HISTORY_LIMIT = 200
PORT = 5001


def load_lxml_html_module():
    try:
        return importlib.import_module("lxml.html")
    except ImportError:
        return None


MARKET_CONFIG = {
    "de": {
        "name": "TopCashback DE",
        "currency": "€",
        "base_url": "https://www.topcashback.de",
        "accept_language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "aff_xpath": '//*[@id="ctl00_ctl29_ctl08_hypMenuItem"]',
        "merchants": [
            "cyberghostvpn",
            "surfshark",
            "f-secure-internet-security-and-vpn",
            "nordvpn",
            "express-vpn",
            "purevpn",
        ],
    },
    "uk": {
        "name": "TopCashback UK",
        "currency": "£",
        "base_url": "https://www.topcashback.co.uk",
        "accept_language": "en-GB,en;q=0.9",
        "rate_xpath": '//*[@id="ctl00_BodyMain_MicroFrontEndControl_pnlContent"]/div[3]/div/div[3]/div[4]/div/div[2]/span',
        "aff_xpath": '//*[@id="ctl00_ctl29_ctl07_hypMenuItem"]',
        "merchants": [
            "cyberghost-vpn",
            "surfshark",
            "nordvpn",
            "expressvpn-uk",
            "purevpn",
        ],
    },
    "us": {
        "name": "TopCashback US",
        "currency": "$",
        "base_url": "https://www.topcashback.com",
        "accept_language": "en-GB,en;q=0.9",
        "rate_xpath": '//*[@id="ctl00_BodyMain_MicroFrontEndControl_pnlContent"]/div[3]/div/div[3]/div[4]/div/div[2]/span',
        "aff_xpath": [
            '//*[@id="ctl00_ctl16_ctl07_hypMenuItem"]',
            '//a[contains(@href, "/account/refer-a-friend/")]',
        ],
        "merchants": [
            "cyberghost-vpn",
            "surfshark",
            "nordvpn",
            "expressvpn",
            "purevpn",
        ],
    },
}


class TopCashbackClient:
    def __init__(
        self,
        base_url,
        accept_language,
        rate_xpath=None,
        aff_xpath=None,
        headers=None,
    ):
        self.base_url = base_url
        self.rate_xpath = rate_xpath
        self.aff_xpath = aff_xpath
        self.headers = headers or {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": accept_language,
        }

    @staticmethod
    def _extract_by_regex(html):
        # Any location has the exactly same tag of cachback rate
        # It is used when the rate xpath didn't defined or work
        match = re.search(
            r'class="merch-cat__rate">\s*(?P<rate>\d+(?:,\d+)?%)\s*<',
            html,
            re.DOTALL,
        )
        if match:
            return match.group("rate")
        return None

    @staticmethod
    def _extract_by_xpath(html, xpath_expression):
        if not xpath_expression:
            return None

        lxml_html_module = load_lxml_html_module()
        if lxml_html_module is None:
            return None

        tree = lxml_html_module.fromstring(html)
        nodes = tree.xpath(xpath_expression)
        if not nodes:
            return None

        node = nodes[0]
        value = node.text_content() if hasattr(node, "text_content") else str(node)
        value = re.sub(r"\s+", " ", value).strip()
        return value or None

    @staticmethod
    def _extract_first_number(text):
        if not text:
            return None
        match = re.search(r"(\d+(?:[\.,]\d+)?)", str(text))
        if not match:
            return None
        return match.group(1).replace(",", ".")

    def _extract_rate(self, html):
        xpath_rate = self._extract_by_xpath(html, self.rate_xpath)
        if xpath_rate:
            return xpath_rate

        regex_rate = self._extract_by_regex(html)
        if regex_rate:
            return regex_rate

        return "Not Found"

    def _extract_aff(self, html):
        xpath_expressions = []
        if isinstance(self.aff_xpath, (list, tuple)):
            xpath_expressions = [expr for expr in self.aff_xpath if expr]
        elif self.aff_xpath:
            xpath_expressions = [self.aff_xpath]

        for xpath_expression in xpath_expressions:
            value = self._extract_by_xpath(html, xpath_expression)
            if value:
                numeric_value = self._extract_first_number(value)
                if numeric_value is not None:
                    return numeric_value

        # Fallback for referral links when IDs or layout change.
        fallback = re.search(
            r'href="[^"]*(?:freunde-werben-freunde|tell-a-friend|refer-a-friend)[^"]*"[^>]*>(?P<text>.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if fallback:
            text = re.sub(r"<[^>]+>", "", fallback.group("text"))
            text = unescape(re.sub(r"\s+", " ", text)).strip()
            if text:
                numeric_value = self._extract_first_number(text)
                if numeric_value is not None:
                    return numeric_value

        return None

    def get_merchant_data(self, merchant_name):
        url = f"{self.base_url}/{merchant_name}/"
        req = urllib.request.Request(url, headers=self.headers)

        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", errors="ignore")

        return {
            "rate": self._extract_rate(html),
            "aff": self._extract_aff(html),
        }

    def get_cashback_rate(self, merchant_name):
        return self.get_merchant_data(merchant_name)["rate"]


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
    for merchant in merchants:
        url = f"{client.base_url}/{merchant}/"
        try:
            merchant_data = client.get_merchant_data(merchant)
            rows.append(
                {
                    "merchant": merchant,
                    "rate": merchant_data.get("rate"),
                    "aff": merchant_data.get("aff"),
                    "error": None,
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
        except urllib.error.URLError as err:
            rows.append(
                {
                    "merchant": merchant,
                    "rate": "N/A",
                    "aff": None,
                    "error": str(err.reason),
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
        except Exception as err:
            rows.append(
                {
                    "merchant": merchant,
                    "rate": "N/A",
                    "aff": None,
                    "error": str(err),
                    "checked_at_utc": checked_at,
                    "url": url,
                }
            )
    return rows


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
    clients = {
        code: TopCashbackClient(
            base_url=config["base_url"],
            accept_language=config["accept_language"],
            rate_xpath=config.get("rate_xpath"),
            aff_xpath=config.get("aff_xpath"),
        )
        for code, config in MARKET_CONFIG.items()
    }

    def worker(market_code):
        monitor = monitors[market_code]
        client = clients[market_code]
        merchants = MARKET_CONFIG[market_code]["merchants"]
        while True:
            monitor.update(poll_once(client, merchants))
            time.sleep(monitor.poll_interval_seconds)

    for code in MARKET_CONFIG:
        monitors[code].update(
            poll_once(clients[code], MARKET_CONFIG[code]["merchants"])
        )
        threading.Thread(target=worker, args=(code,), daemon=True).start()

    app = Flask(__name__)

    @app.get("/")
    def index():
        markets = [
            {
                "code": code,
                "name": config["name"],
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
                        "currency": config.get("currency", ""),
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
    app.run(host="0.0.0.0", port=PORT, debug=False)
