"""Минимальный клиент Bot API: getUpdates, sendMessage, sendPhoto с лимитами."""
import logging
import time

import requests

log = logging.getLogger("tg")
API = "https://api.telegram.org"


class Telegram:
    def __init__(self, token: str):
        self.token = token
        self._last_send = 0.0
        self._dropped_webhook = False

    def _call(self, method, params=None, timeout=35):
        r = requests.post(f"{API}/bot{self.token}/{method}", json=params or {},
                          timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            return {"ok": False, "description": f"HTTP {r.status_code}"}
        if r.status_code == 429:
            data["_retry_after"] = data.get("parameters", {}).get("retry_after", 5)
        return data

    def drop_webhook(self):
        if not self._dropped_webhook:
            self._call("deleteWebhook", {"drop_pending_updates": False})
            self._dropped_webhook = True

    def get_updates(self, offset: int, limit: int = 50):
        return self._call("getUpdates", {
            "offset": offset, "limit": limit, "timeout": 0,
            "allowed_updates": ["message"],
        }, timeout=30)

    def send_text(self, chat_id, text: str):
        return self._call("sendMessage", {
            "chat_id": chat_id, "text": text[:4000], "no_webpage": True,
        })

    def _throttle(self, per_chat=None):
        """~1 сообщение/сек глобально."""
        wait = 1.05 - (time.time() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        self._last_send = time.time()

    def send_card(self, chat_id, caption: str, photo_url=None,
                  reply_markup=None):
        """Фото + подпись + inline-кнопка; 429 с RetryAfter; фолбэк на текст."""
        caption = caption[:1024]
        for attempt in range(2):
            self._throttle()
            if photo_url:
                r = self._call("sendPhoto", {
                    "chat_id": chat_id, "photo": photo_url, "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup,
                })
                if r.get("ok"):
                    return True
                log.warning("sendPhoto failed: %s", r.get("description"))
                photo_url = None  # фолбэк текстом
                continue
            r = self._call("sendMessage", {
                "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                "no_webpage": True, "reply_markup": reply_markup,
            })
            if r.get("ok"):
                return True
            if r.get("_retry_after") and attempt == 0:
                time.sleep(r["_retry_after"] + 0.5)
                continue
            log.warning("sendMessage failed: %s", r.get("description"))
            return False
        return False
