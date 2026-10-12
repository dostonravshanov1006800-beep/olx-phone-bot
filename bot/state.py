"""Состояние бота в JSON-файлах внутри data/ (переживает рестарты,
коммитится в репозиторий — на GitHub Actions это замена SQLite).

data/state.json  — offset, baseline, курс USD, время последнего алерта
data/subs.json    — подписчики (ключ — sha256(chat_id), значение {enabled,...})
data/ads.json     — виденные объявления (дедупликация, цены, отметки отправки)
data/history.json — история цен по группам (модель+память+состояние)
"""
import hashlib
import json
import time
from pathlib import Path


def chat_hash(chat_id) -> str:
    return hashlib.sha256(f"olx-ph:{chat_id}".encode()).hexdigest()[:20]


def load(data_dir: Path):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    def _read(name, default):
        p = data_dir / name
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (ValueError, OSError):
                pass
        return default

    return {
        "state": _read("state.json", {
            "offset": 0, "baseline_done": False, "usd_rate": {},
            "last_alert": 0,
        }),
        "subs": _read("subs.json", {}),
        "ads": _read("ads.json", {}),
        "history": _read("history.json", {}),
    }


def save(data_dir, db):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (("state.json", db["state"]),
                          ("subs.json", db["subs"]),
                          ("ads.json", db["ads"]),
                          ("history.json", db["history"])):
        tmp = data_dir / (name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(data_dir / name)


def now() -> int:
    return int(time.time())


def cleanup(db, ads_days=60, history_days=30):
    """Чистка: объявления старше 60 дней, история цен старше 30 дней."""
    horizon_ads = now() - ads_days * 86400
    horizon_hist = now() - history_days * 86400
    before = len(db["ads"])
    db["ads"] = {k: a for k, a in db["ads"].items()
                 if a.get("first_seen", 0) >= horizon_ads
                 or a.get("last_seen", 0) >= horizon_ads}
    removed = before - len(db["ads"])
    for group, points in list(db["history"].items()):
        kept = [p for p in points if p[0] >= horizon_hist]
        if kept:
            db["history"][group] = kept[-500:]
        else:
            del db["history"][group]
    return removed
