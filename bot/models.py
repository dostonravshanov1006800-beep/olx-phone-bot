"""Работа с моделями из whitelist + извлечение полей из заголовка."""
import re

STORAGE_RE = re.compile(r"(\d{1,4})\s*(гб|gb|тб|tb)\b", re.I)
PAIR_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{2,4})\s*(?:гб|gb|tb|тб)?\b", re.I)


class ModelMatcher:
    def __init__(self, cfg):
        self.junk = [k.lower() for k in cfg.junk_keywords]
        self.compiled = {}  # brand -> [(pattern, compiled]
        for brand, patterns in cfg.brands.items():
            self.compiled[brand] = [
                (p, re.compile(p, re.I)) for p in patterns
            ]
        self.extras_kw = cfg.extras_keywords

    def is_junk(self, title: str) -> bool:
        t = title.lower()
        return any(k in t for k in self.junk)

    def match(self, title: str):
        """Возвращает (brand, model_norm) или None."""
        if self.is_junk(title):
            return None
        for brand, pats in self.compiled.items():
            for _, c in pats:
                m = c.search(title)
                if m:
                    model = re.sub(r"\s+", "", m.group(0).lower())
                    return brand, model
        return None

    def storage(self, title: str):
        """Объём памяти в GB (int) или None."""
        best = 0
        for m in PAIR_RE.finditer(title):
            for g in m.group(1), m.group(2):
                v = int(g)
                if 32 <= v <= 1024 and v > best:
                    best = v
        for m in STORAGE_RE.finditer(title):
            v = int(m.group(1))
            unit = m.group(2).lower()
            if unit in ("тб", "tb"):
                v *= 1024
            if 32 <= v <= 2048 and v > best:
                best = v
        return best or None

    def extras(self, title: str) -> str:
        t = title.lower()
        parts = []
        if any(k in t for k in self.extras_kw.get("box", [])):
            parts.append("Коробка")
        if any(k in t for k in self.extras_kw.get("receipt", [])):
            parts.append("Чек")
        if any(k in t for k in self.extras_kw.get("warranty", [])):
            parts.append("Гарантия")
        return ", ".join(parts)

    @staticmethod
    def group_key(brand: str, model: str, storage, condition: str) -> str:
        return f"{brand}|{model}|{storage or '-'}|{condition}"
