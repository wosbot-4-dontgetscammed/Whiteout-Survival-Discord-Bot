# Centralized configuration for WOS Bot
# All shared constants and config values in one place

import os
from .log_config import get_logger

_logger = get_logger("config")

# WOS API encryption key
WOS_ENCRYPT_KEY = os.getenv("WOS_ENCRYPT_KEY", "")

# WOS API endpoints
WOS_PLAYER_INFO_URL = "https://wos-giftcode-api.centurygame.com/api/player"
WOS_GIFTCODE_URL = "https://wos-giftcode-api.centurygame.com/api/gift_code"
WOS_CAPTCHA_URL = "https://wos-giftcode-api.centurygame.com/api/captcha"
WOS_GIFTCODE_REDEMPTION_URL = "https://wos-giftcode.centurygame.com"

# Required headers for CenturyGame API (Referer check added ~2026)
WOS_API_HEADERS = {
    "content-type": "application/x-www-form-urlencoded",
    "referer": "https://wos-giftcode.centurygame.com/",
}

# Test player ID used for gift code validation
WOS_TEST_PLAYER_ID = os.getenv("WOS_TEST_PLAYER_ID", "244886619")

# Gift Code Community API
WOSLAND_API_URL = os.getenv("GIFTCODE_API_URL", "")
WOSLAND_API_KEY = os.getenv("GIFTCODE_API_KEY", "")


def validate_config():
    """Warn about missing critical config values at startup."""
    if not WOS_ENCRYPT_KEY:
        _logger.warning("WOS_ENCRYPT_KEY not set in .env - API requests will fail")
    if not WOSLAND_API_KEY:
        _logger.info("GIFTCODE_API_KEY not set - community gift code sync disabled")

# Furnace level mapping (shared across multiple cogs)
LEVEL_MAPPING = {
    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
    35: "FC 1", 36: "FC 1 - 1", 37: "FC 1 - 2", 38: "FC 1 - 3", 39: "FC 1 - 4",
    40: "FC 2", 41: "FC 2 - 1", 42: "FC 2 - 2", 43: "FC 2 - 3", 44: "FC 2 - 4",
    45: "FC 3", 46: "FC 3 - 1", 47: "FC 3 - 2", 48: "FC 3 - 3", 49: "FC 3 - 4",
    50: "FC 4", 51: "FC 4 - 1", 52: "FC 4 - 2", 53: "FC 4 - 3", 54: "FC 4 - 4",
    55: "FC 5", 56: "FC 5 - 1", 57: "FC 5 - 2", 58: "FC 5 - 3", 59: "FC 5 - 4",
    60: "FC 6", 61: "FC 6 - 1", 62: "FC 6 - 2", 63: "FC 6 - 3", 64: "FC 6 - 4",
    65: "FC 7", 66: "FC 7 - 1", 67: "FC 7 - 2", 68: "FC 7 - 3", 69: "FC 7 - 4",
    70: "FC 8", 71: "FC 8 - 1", 72: "FC 8 - 2", 73: "FC 8 - 3", 74: "FC 8 - 4",
    75: "FC 9", 76: "FC 9 - 1", 77: "FC 9 - 2", 78: "FC 9 - 3", 79: "FC 9 - 4",
    80: "FC 10", 81: "FC 10 - 1", 82: "FC 10 - 2", 83: "FC 10 - 3", 84: "FC 10 - 4",
}

# Furnace level emoji IDs (Discord custom emojis)
FL_EMOJIS = {
    range(35, 40): "<:fc1:1326751863764156528>",
    range(40, 45): "<:fc2:1326751886954594315>",
    range(45, 50): "<:fc3:1326751903912034375>",
    range(50, 55): "<:fc4:1326751938674692106>",
    range(55, 60): "<:fc5:1326751952750776331>",
    range(60, 65): "<:fc6:1326751966184869981>",
    range(65, 70): "<:fc7:1326751983939489812>",
    range(70, 75): "<:fc8:1326751996707082240>",
    range(75, 80): "<:fc9:1326752008505528331>",
    range(80, 85): "<:fc10:1326752023001174066>",
}
