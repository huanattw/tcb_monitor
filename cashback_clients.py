import importlib
import json
import re
import urllib.request
from html import unescape


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def load_lxml_html_module():
    try:
        return importlib.import_module("lxml.html")
    except ImportError:
        return None


class CashbackClient:
    def __init__(self, base_url, accept_language, headers=None, **_kwargs):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": accept_language,
        }

    def build_url(self, merchant_slug):
        return f"{self.base_url}/{merchant_slug}/"

    def fetch_html(self, merchant_slug):
        request = urllib.request.Request(
            self.build_url(merchant_slug),
            headers=self.headers,
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")

    def get_merchant_data(self, merchant_slug):
        raise NotImplementedError


class TopCashbackClient(CashbackClient):
    def __init__(self, rate_xpath=None, aff_xpath=None, **kwargs):
        super().__init__(**kwargs)
        self.rate_xpath = rate_xpath
        self.aff_xpath = aff_xpath

    @staticmethod
    def _extract_by_regex(html):
        match = re.search(
            r'class="merch-cat__rate">\s*(?P<rate>\d+(?:,\d+)?%)\s*<',
            html,
            re.DOTALL,
        )
        return match.group("rate") if match else None

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
        return re.sub(r"\s+", " ", value).strip() or None

    @staticmethod
    def _extract_first_number(text):
        if not text:
            return None
        match = re.search(r"(\d+(?:[\.,]\d+)?)", str(text))
        return match.group(1).replace(",", ".") if match else None

    def _extract_rate(self, html):
        return (
            self._extract_by_xpath(html, self.rate_xpath)
            or self._extract_by_regex(html)
            or "Not Found"
        )

    def _extract_aff(self, html):
        expressions = (
            list(self.aff_xpath)
            if isinstance(self.aff_xpath, (list, tuple))
            else [self.aff_xpath]
        )
        for expression in filter(None, expressions):
            value = self._extract_by_xpath(html, expression)
            numeric_value = self._extract_first_number(value)
            if numeric_value is not None:
                return numeric_value

        fallback = re.search(
            r'href="[^"]*(?:freunde-werben-freunde|tell-a-friend|refer-a-friend|invita-un-amico)[^"]*"[^>]*>(?P<text>.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if fallback:
            text = re.sub(r"<[^>]+>", "", fallback.group("text"))
            return self._extract_first_number(
                unescape(re.sub(r"\s+", " ", text)).strip()
            )
        return None

    def get_merchant_data(self, merchant_slug):
        html = self.fetch_html(merchant_slug)
        return {"rate": self._extract_rate(html), "aff": self._extract_aff(html)}


class ShopBackClient(CashbackClient):
    def build_url(self, merchant_slug):
        return f"{self.base_url}/{merchant_slug}"

    @staticmethod
    def _extract_rate(html):
        # This is the visible primary offer and is more stable than generated CSS.
        current_offer = re.search(
            r'data-testid="current-offer"[^>]*>\s*([^<]+?)\s*</',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if current_offer:
            text = unescape(current_offer.group(1))
            match = re.search(r"(\d+(?:[.,]\d+)?\s*%)", text)
            if match:
                return re.sub(r"\s+", "", match.group(1))

        # Server-rendered JSON-LD remains a useful fallback if markup changes.
        for raw_json in re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(unescape(raw_json))
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("@type") != "OfferCatalog":
                continue
            prices = []
            for offer in data.get("offers", []):
                for spec in offer.get("priceSpecification", []):
                    try:
                        prices.append(float(str(spec.get("price")).replace(",", ".")))
                    except (TypeError, ValueError):
                        pass
            if prices:
                value = max(prices)
                return f"{value:g}%"

        title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title:
            match = re.search(r"(\d+(?:[.,]\d+)?\s*%)", unescape(title.group(1)))
            if match:
                return re.sub(r"\s+", "", match.group(1))
        return "Not Found"

    def get_merchant_data(self, merchant_slug):
        return {"rate": self._extract_rate(self.fetch_html(merchant_slug)), "aff": None}


CLIENTS = {
    "topcashback": TopCashbackClient,
    "shopback": ShopBackClient,
}


def create_client(config):
    provider = config.get("provider", "topcashback")
    try:
        client_class = CLIENTS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown cashback provider: {provider}") from exc

    return client_class(
        base_url=config["base_url"],
        accept_language=config["accept_language"],
        rate_xpath=config.get("rate_xpath"),
        aff_xpath=config.get("aff_xpath"),
    )
