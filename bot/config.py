"""OLX Phone Hunter bot — configuration from environment + models.yaml."""
import os
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Config:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.admin_chat_id = os.environ.get("ADMIN_CHAT_ID", "").strip()
        self.scan_pages = int(os.environ.get("SCAN_PAGES", "2"))
        self.max_ads_per_cycle = int(os.environ.get("MAX_ADS_PER_CYCLE", "30"))
        self.deal_min_discount_pct = float(os.environ.get("DEAL_MIN_DISCOUNT_PCT", "15"))
        self.hot_discount_pct = float(os.environ.get("HOT_DISCOUNT_PCT", "25"))
        self.suspicious_below_pct = float(os.environ.get("SUSPICIOUS_BELOW_PCT", "40"))
        # "skip" — не отправлять подозрительно дешёвые, "send" — отправлять с меткой ⚠️
        self.suspicious_mode = os.environ.get("SUSPICIOUS_MODE", "skip").strip().lower()
        self.fallback_usd_rate = float(os.environ.get("FALLBACK_USD_RATE", "11850"))
        self.scan_interval_sec = int(os.environ.get("SCAN_INTERVAL_SEC", "120"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

        p = ROOT / "models.yaml"
        with open(p, encoding="utf-8") as f:
            y = yaml.safe_load(f)
        self.brands = y["brands"]            # {brand: [regex patterns]}
        self.junk_keywords = y["junk_keywords"]
        self.extras_keywords = y.get("extras_keywords", {})

    @property
    def data_dir(self):
        return ROOT / "data"
