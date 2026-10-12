"""OLX Phone Hunter — точка входа.

GitHub Actions (эфемерный запуск): python bot/main.py -> один цикл
(обработка команд, скан OLX, рассылка, коммит состояния в репо).

VPS / Docker: python bot/main.py --loop -> вечный цикл с интервалом
SCAN_INTERVAL_SEC и джиттером ±20%.
"""
import html
import logging
import random
import re
import subprocess
import sys
import time

from bot.config import Config
from bot.models import ModelMatcher
from bot import olx, pricing, state as st, telegram

log = logging.getLogger("main")

AUTO_RE = re.compile(r"^/auto[_ ]?search\b", re.I)
ALERT_EVERY_SEC = 6 * 3600
MIN_SAMPLES = 5
HIST_CAP = 200


def fmt(n) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def setup_logging(level):
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def enabled_chats(db):
    return [k for k, v in db["subs"].items() if v.get("enabled")]


def process_updates(tg, db, cfg):
    res = tg.get_updates(db["state"]["offset"])
    if not res.get("ok"):
        log.warning("getUpdates failed: %s", res.get("description"))
        return
    for upd in res.get("result", []):
        db["state"]["offset"] = upd["update_id"] + 1
        msg = upd.get("message")
        if not msg:
            continue
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        key = st.chat_hash(chat_id)
        sub = db["subs"].setdefault(key, {"enabled": False,
                                          "since": st.now()})
        if text.startswith("/start"):
            tg.send_text(chat_id, "Нажми /auto_search, чтобы включить авто-поиск")
        elif AUTO_RE.match(text):
            sub["enabled"] = not sub.get("enabled")
            tg.send_text(chat_id, "Авто-поиск включён ✅" if sub["enabled"]
                         else "Авто-поиск выключен ⛔")
        # любые другие команды/сообщения игнорируются


def market_median(history, ad):
    """Медиана: группа (модель+память+состояние) -> расширение до
    (модель+состояние) -> None, если данных всё ещё мало."""
    pts = history.get(ad["group"], [])
    if len(pts) >= MIN_SAMPLES:
        return pricing.median_market([p for p, _ in pts])
    wide = []
    for k, v in history.items():
        b, m, _s, c = k.split("|", 3)
        if b == ad["brand"] and m == ad["model"] and c == ad["condition"]:
            wide.extend(v)
    if len(wide) >= MIN_SAMPLES:
        return pricing.median_market([p for p, _ in wide])
    return None


def build_caption(ad, median_uzs, score, tag, suspicious=False):
    price, cur = ad["price"], ad["currency"]
    lines = [f"{tag} <b>{html.escape(ad['title'])}</b> — {ad['condition']}"]
    if cur == "USD":
        pline = f"💰 ${fmt(price)}"
        if median_uzs:
            pline += (f"  (рынок ≈ {fmt(median_uzs)} сум, −{abs(score)}%)"
                      if score > 0 else f"  (рынок ≈ {fmt(median_uzs)} сум)")
    else:
        pline = f"💰 {fmt(price)} сум"
        if median_uzs:
            pline += (f"  (рынок ≈ {fmt(median_uzs)} сум, −{abs(score)}%)"
                      if score > 0 else f"  (рынок ≈ {fmt(median_uzs)} сум)")
    lines.append(pline)
    if suspicious:
        lines.append("⚠️ Подозрительно дёшево (возможен обман)")
    where = "📍 " + ad["city"] if ad["city"] else "📍 Узбекистан"
    if ad.get("posted"):
        where += f" · 🕒 {ad['posted']}"
    lines.append(where)
    if ad.get("extras"):
        lines.append(f"📦 {ad['extras']}")
    return "\n".join(lines)


def kb_open(ad):
    return {"inline_keyboard": [[
        {"text": "Открыть на OLX", "url": ad["url"]}]]}


def scan_cycle(db, cfg, matcher, rate):
    """Скан выдачи, дедупликация, принятие решений о рассылке.

    Возвращает список (ad, median, score, tag, suspicious).
    """
    raw = []
    for page in range(1, cfg.scan_pages + 1):
        raw.extend(olx.parse_ads(olx.fetch_listing(page)))
        time.sleep(random.uniform(1.0, 2.0))  # вежливая пауза между запросами

    by_id = {}
    for ad in raw:
        by_id.setdefault(ad["id"], ad)

    matched = 0
    for ad in by_id.values():
        m = matcher.match(ad["title"])
        if not m:
            continue
        ad["brand"], ad["model"] = m
        ad["storage"] = matcher.storage(ad["title"])
        ad["extras"] = matcher.extras(ad["title"])
        ad["group"] = matcher.group_key(ad["brand"], ad["model"],
                                        ad["storage"], ad["condition"])
        matched += 1
    log.info("скан: %d объявлений, %d по моделям 2021+",
             len(by_id), matched)

    sends = []
    for ad in by_id.values():
        if "group" not in ad:
            continue
        price_uzs = pricing.to_uzs(ad["price"], ad["currency"], rate)
        if price_uzs <= 0:
            continue
        hist = db["history"].setdefault(ad["group"], [])
        hist.append([price_uzs, st.now()])
        if len(hist) > HIST_CAP:
            del hist[: len(hist) - HIST_CAP]

        median = market_median(db["history"], ad)
        score = pricing.deal_score(price_uzs, median) if median else 0.0
        suspicious_limit = (median * cfg.suspicious_below_pct / 100.0
                            if median else 0)

        existing = db["ads"].get(ad["id"])
        if existing is None:
            db["ads"][ad["id"]] = {
                "url": ad["url"], "title": ad["title"], "brand": ad["brand"],
                "model": ad["model"], "storage": ad["storage"],
                "condition": ad["condition"], "price": ad["price"],
                "currency": ad["currency"], "city": ad["city"],
                "photo": ad["photo"], "group": ad["group"],
                "first_seen": st.now(), "last_seen": st.now(),
                "first_price_uzs": price_uzs,
                "sent_price": None, "sent_at": None,
            }
            if not db["state"]["baseline_done"]:
                continue  # первый запуск: только запомнить, без отправки
            if median:
                if price_uzs < suspicious_limit:
                    if cfg.suspicious_mode == "send":
                        sends.append((ad, median, score, "⚠️", True))
                        _mark_sent(db, ad, price_uzs)
                elif score >= cfg.deal_min_discount_pct:
                    tag = ("🔥" if score >= cfg.hot_discount_pct else "🤑")
                    sends.append((ad, median, score, tag, False))
                    _mark_sent(db, ad, price_uzs)
        else:
            existing["last_seen"] = st.now()
            existing["price"] = ad["price"]
            existing["currency"] = ad["currency"]
            sent = existing.get("sent_price")
            if sent and price_uzs <= sent * 0.95:
                # цена снизилась -> повторная отправка с меткой 📉
                sends.append((ad, median, score, "📉", False))
                _mark_sent(db, ad, price_uzs)
            elif (not sent and median and price_uzs
                    <= existing.get("first_price_uzs", price_uzs) * 0.95):
                # цена реально снизилась с первого взгляда и стала выгодной
                if (price_uzs >= suspicious_limit
                        and score >= cfg.deal_min_discount_pct):
                    tag = ("🔥" if score >= cfg.hot_discount_pct else "🤑")
                    sends.append((ad, median, score, tag, False))
                    _mark_sent(db, ad, price_uzs)
    return sends


def _mark_sent(db, ad, price_uzs):
    rec = db["ads"].get(ad["id"])
    if rec:
        rec["sent_price"] = price_uzs
        rec["sent_at"] = st.now()


def deliver(tg, db, cfg, sends):
    chats = enabled_chats(db)
    if not chats:
        return 0
    sends.sort(key=lambda s: -s[2])  # сначала самые выгодные
    sent_total = 0
    for ad, median, score, tag, suspicious in sends[: cfg.max_ads_per_cycle]:
        caption = build_caption(ad, median, score, tag, suspicious)
        kb = kb_open(ad)
        for key in chats:
            if tg.send_card(key, caption, ad.get("photo"), kb):
                sent_total += 1
    return sent_total


def commit_state():
    """Коммит data/ в репозиторий (GitHub Actions)."""
    try:
        subprocess.run(["git", "add", "data/"], check=False,
                       stdout=subprocess.DEVNULL)
        if subprocess.call(["git", "diff", "--cached", "--quiet"],
                           stdout=subprocess.DEVNULL) == 0:
            return False  # изменений нет
        subprocess.run(["git", "commit", "-qm", "state update"],
                       check=False, stdout=subprocess.DEVNULL)
        for _ in range(3):
            if subprocess.call(["git", "push", "-q"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0:
                return True
            subprocess.run(["git", "pull", "--rebase", "-q"], check=False,
                           stdout=subprocess.DEVNULL)
            time.sleep(2)
        log.warning("push не удался после ретраев")
        return False
    except Exception as e:
        log.warning("commit_state failed: %s", e)
        return False


def cycle(cfg):
    db = st.load(cfg.data_dir)
    tg = telegram.Telegram(cfg.token)
    tg.drop_webhook()
    process_updates(tg, db, cfg)

    try:
        if db["state"]["baseline_done"] and not enabled_chats(db):
            log.info("нет активных подписок — скан пропущен")
        else:
            rate = pricing.usd_rate(db["state"], cfg)
            matcher = ModelMatcher(cfg)
            sends = scan_cycle(db, cfg, matcher, rate)
            if db["state"]["baseline_done"]:
                n = deliver(tg, db, cfg, sends)
                log.info("отправлено карточек: %d", n)
            else:
                db["state"]["baseline_done"] = True
                log.info("baseline: %d объявлений запомнено без отправки",
                         len(db["ads"]))
    except olx.OlxBlocked as e:
        log.error("OLX недоступен: %s", e)
        if (cfg.admin_chat_id and st.now() - db["state"].get("last_alert", 0)
                > ALERT_EVERY_SEC):
            try:
                tg.send_text(cfg.admin_chat_id,
                             "⚠️ OLX.uz блокирует запросы бота (403/лимит). "
                             "Продолжаю попытки, объявления не приходят, "
                             "пока блок не снят.")
                db["state"]["last_alert"] = st.now()
            except Exception:
                pass
    removed = st.cleanup(db)
    if removed:
        log.info("очищено старых объявлений: %d", removed)
    st.save(cfg.data_dir, db)


def main():
    cfg = Config()
    setup_logging(cfg.log_level)
    if not cfg.token:
        log.error("TELEGRAM_BOT_TOKEN не задан")
        sys.exit(1)
    if "--loop" not in sys.argv:
        cycle(cfg)
        commit_state()
        return
    while True:  # режим VPS: 24/7 с автоперезапуском цикла
        try:
            cycle(cfg)
            commit_state()
        except Exception:
            log.exception("цикл упал, продолжаю")
        time.sleep(cfg.scan_interval_sec * random.uniform(0.8, 1.2))


if __name__ == "__main__":
    main()
