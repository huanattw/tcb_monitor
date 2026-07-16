"""Monitoring targets.

To add a merchant, append one item to the relevant market's ``merchants`` list:

    {"slug": "merchant-url-slug", "name": "Display Name"}

If ``name`` is omitted, the slug is used as the display name.
"""


MARKET_CONFIG = {
    "de": {
        "name": "TopCashback DE",
        "provider": "topcashback",
        "currency": "€",
        "base_url": "https://www.topcashback.de",
        "accept_language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "aff_xpath": '//*[@id="ctl00_ctl29_ctl08_hypMenuItem"]',
        "merchants": [
            {"slug": "cyberghostvpn"},
            {"slug": "surfshark"},
            {"slug": "f-secure-internet-security-and-vpn"},
            {"slug": "nordvpn"},
            {"slug": "express-vpn"},
            {"slug": "purevpn"},
        ],
    },
    "uk": {
        "name": "TopCashback UK",
        "provider": "topcashback",
        "currency": "£",
        "base_url": "https://www.topcashback.co.uk",
        "accept_language": "en-GB,en;q=0.9",
        "rate_xpath": '//*[@id="ctl00_BodyMain_MicroFrontEndControl_pnlContent"]/div[3]/div/div[3]/div[4]/div/div[2]/span',
        "aff_xpath": '//*[@id="ctl00_ctl29_ctl07_hypMenuItem"]',
        "merchants": [
            {"slug": "cyberghost-vpn"},
            {"slug": "surfshark"},
            {"slug": "nordvpn"},
            {"slug": "expressvpn-uk"},
            {"slug": "purevpn"},
        ],
    },
    "us": {
        "name": "TopCashback US",
        "provider": "topcashback",
        "currency": "$",
        "base_url": "https://www.topcashback.com",
        "accept_language": "en-GB,en;q=0.9",
        "rate_xpath": '//*[@id="ctl00_BodyMain_MicroFrontEndControl_pnlContent"]/div[3]/div/div[3]/div[4]/div/div[2]/span',
        "aff_xpath": [
            '//*[@id="ctl00_ctl16_ctl07_hypMenuItem"]',
            '//a[contains(@href, "/account/refer-a-friend/")]',
        ],
        "merchants": [
            {"slug": "cyberghost-vpn"},
            {"slug": "surfshark"},
            {"slug": "expressvpn"},
            {"slug": "purevpn"},
        ],
    },
    "it": {
        "name": "TopCashback IT",
        "provider": "topcashback",
        "currency": "€",
        "base_url": "https://topcashback.it",
        "accept_language": "en-GB,en;q=0.9",
        "rate_xpath": '//*[@id="ctl00_BodyMain_MicroFrontEndControl_pnlContent"]/div[3]/div/div[3]/div[4]/div/div[2]/span',
        "aff_xpath": [
            '//*[@id="ctl00_ctl15_ctl03_hypMenuItem"]',
            '//a[contains(@href, "/account/invita-un-amico/")]',
        ],
        "merchants": [
            {"slug": "cyberghost-vpn"},
            {"slug": "surfshark"},
            {"slug": "expressvpn"},
            {"slug": "purevpn"},
        ],
    },
    "shopback_de": {
        "name": "ShopBack DE",
        "provider": "shopback",
        "supports_aff": False,
        "currency": "€",
        "base_url": "https://www.shopback.de",
        "accept_language": "de-DE,de;q=0.9,en;q=0.8",
        "merchants": [
            {"slug": "surfshark", "name": "Surfshark"},
            {"slug": "nordvpn", "name": "NordVPN"},
            {"slug": "cyberghost-vpn", "name": "CyberGhost VPN"},
            {"slug": "expressvpn", "name": "ExpressVPN"},
        ],
    },
    "shopback_us": {
        "name": "ShopBack US",
        "provider": "shopback",
        "supports_aff": False,
        "currency": "$",
        "base_url": "https://www.shopback.com",
        "accept_language": "en-US,en;q=0.9",
        "merchants": [
            {"slug": "cyberghost-vpn", "name": "CyberGhost VPN"},
            {"slug": "nordvpn", "name": "NordVPN"},
            {"slug": "surfshark", "name": "Surfshark"},
            {"slug": "expressvpn", "name": "ExpressVPN"},
        ],
    },
}
