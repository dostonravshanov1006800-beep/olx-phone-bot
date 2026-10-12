"""Сверка цены с рынком: медианы по истории, IQR, Deal Score, курс USD."""
import datetime
import logging
import statistics

import requests

log = logging.getLogger("pricing")

RATE_API = "https://open.er-api.com/v6/latest/USD"


def usd_rate(state, cfg):
    """Курс USD -> UZS: кэш на сутки в state, иначе API, иначе fallback."""
    today = datetime.date.today().isoformat()
    r = state.get("usd_rate") or {}
    if r.get("date") == today and r.get("value"):
        return r["value"]
    try:
        resp = requests.get(RATE_API, timeout=15)
        v = float(resp.json()["rates"]["UZS"])
        if v > 1000:
            state["usd_rate"] = {"date": today, "value": v}
            return v
    except Exception as e:
        log.warning("rate fetch failed: %s", e)
    return cfg.fallback_usd_rate


def to_uzs(price: float, currency: str, rate: float) -> float:
    return price * rate if currency == "USD" else price


def median_market(history_prices):
    """Медиана после очистки выбросов методом IQR. None — если мало данных."""
    if not history_prices:
        return None
    prices = sorted(history_prices)
    if len(prices) >= 5:
        q = (len(prices) - 1) / 4
        def qv(p, i):
            lo, hi = int(i), min(int(i) + 1, len(p) - 1)
            return p[lo] + (p[hi] - p[lo]) * (i - lo)
        q1, q3 = qv(prices, q), qv(prices, 3 * q)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        filtered = [p for p in prices if lo <= p <= hi]
        if len(filtered) >= 5:
            prices = filtered
    return statistics.median(prices)


def deal_score(price_uzs: float, median: float):
    """Процент ниже рынка (положительный = дешевле рынка)."""
    if not median or median <= 0:
        return 0.0
    return round((median - price_uzs) / median * 100.0, 1)
