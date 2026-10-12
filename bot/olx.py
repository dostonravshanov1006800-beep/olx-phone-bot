"""Получение списка объявлений с OLX.uz и парсинг карточек.

Прямой запрос (работает, если IP не заблокирован, например на VPS),
иначе — Jina Reader (r.jina.ai), который отдаёт страницу в markdown.
Этично: только публичная выдача, паузы между запросами, ретраи с backoff.
"""
import logging
import random
import re
import time

import requests

log = logging.getLogger("olx")

LISTING_URL = "https://www.olx.uz/elektronika/telefony/mobilnye-telefony/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

CARD_HEADER_RE = re.compile(r"^####\s+\[(.+?)\]\((https?://[^\s)]+)\)\s*$", re.M)
PRICE_RE = re.compile(r"^([\d\s\u00a0.]+)\s*(сум|у\.е\.)\s*$", re.M)
COND_RE = re.compile(r"^(Новый|Б/у|Б/У)\s*$", re.M)
LOC_RE = re.compile(r"^(.+?)\s+-\s+(Сегодня[^\n]*|Вчера[^\n]*|[0-9]+ [^\n]*г\.)\s*$", re.M)
IMG_RE = re.compile(r"!\[Image \d+:[^\]]*\]\((https?://[^\s)]+)\)")
ID_RE = re.compile(r"-ID([0-9A-Za-z]+)\.html")


class OlxBlocked(Exception):
    pass


def _direct_fetch(url: str) -> str:
    r = requests.get(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }, timeout=20)
    if r.status_code == 200 and "d/obyavlenie" in r.text:
        return r.text
    raise OlxBlocked(f"direct HTTP {r.status_code}")


def _jina_fetch(url: str) -> str:
    r = requests.get("https://r.jina.ai/" + url, headers={
        "User-Agent": UA, "Accept": "text/plain",
    }, timeout=60)
    if r.status_code == 200 and "d/obyavlenie" in r.text:
        return r.text
    raise OlxBlocked(f"jina HTTP {r.status_code}")


def fetch_listing(page: int = 1) -> str:
    """Страница выдачи (markdown/html). Ретраи с backoff, блок -> OlxBlocked."""
    url = LISTING_URL if page <= 1 else f"{LISTING_URL}?page={page}"
    last = None
    for attempt in range(3):
        for fetcher in (_direct_fetch, _jina_fetch):
            try:
                return fetcher(url)
            except (OlxBlocked, requests.RequestException) as e:
                last = e
                log.debug("fetch attempt failed (%s): %s", fetcher.__name__, e)
        time.sleep((2 ** attempt) * 3 + random.uniform(0, 2))
    raise OlxBlocked(f"all attempts failed: {last}")


def parse_ads(text: str):
    """Markdown выдачи -> список dict'ов с карточками объявлений."""
    headers = list(CARD_HEADER_RE.finditer(text))
    ads = []
    for i, h in enumerate(headers):
        url = h.group(2)
        mid = ID_RE.search(url)
        if not mid:
            continue
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        # фото: markdown-картинка стоит перед заголовком карты
        pre = text[max(0, h.start() - 400):h.start()]
        img = None
        for im in IMG_RE.finditer(pre):
            cand = im.group(1)
            if "no_thumbnail" not in cand:
                img = cand
        price_m = PRICE_RE.search(block)
        if not price_m:
            continue  # нет цены / договорная
        price = int(re.sub(r"[^\d]", "", price_m.group(1)))
        if price <= 0:
            continue
        currency = "USD" if price_m.group(2).startswith("у") else "UZS"
        cond_m = COND_RE.search(block)
        condition = "Новый" if cond_m and cond_m.group(1) == "Новый" else "Б/у"
        loc_m = LOC_RE.search(block)
        city = loc_m.group(1).strip() if loc_m else ""
        posted = loc_m.group(2).strip() if loc_m else ""
        # крупное фото для Telegram
        photo = None
        if img:
            photo = re.sub(r";s=\d+x\d+;q=\d+", ";s=800x600;q=80", img)
        ads.append({
            "id": mid.group(1),
            "url": url,
            "title": h.group(1).strip(),
            "price": price,
            "currency": currency,
            "condition": condition,
            "city": city,
            "posted": posted,
            "photo": photo,
        })
    return ads
