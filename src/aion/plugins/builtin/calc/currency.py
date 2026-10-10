"""Currency conversion with Central Bank of Russia daily rates (no API key)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from aion.text import normalize
from aion.text.plural import plural

RATES_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CACHE_SECONDS = 3600

_GBP = ("фунт стерлингов", "фунта стерлингов", "фунтов стерлингов")

# (stems, code, forms)
CURRENCIES: tuple[tuple[tuple[str, ...], str, tuple[str, str, str]], ...] = (
    (("бел",), "BYN", ("белорусский рубль", "белорусских рубля", "белорусских рублей")),
    (("руб", "rub", "₽"), "RUB", ("рубль", "рубля", "рублей")),
    (("долл", "бакс", "usd", "$"), "USD", ("доллар", "доллара", "долларов")),
    (("евро", "eur", "€"), "EUR", ("евро", "евро", "евро")),
    (("юан", "cny"), "CNY", ("юань", "юаня", "юаней")),
    (("фунт стерл", "стерлинг", "gbp"), "GBP", _GBP),
    (("тенге", "kzt"), "KZT", ("тенге", "тенге", "тенге")),
    (("гривн", "uah"), "UAH", ("гривна", "гривны", "гривен")),
    (("иен", "йен", "jpy"), "JPY", ("иена", "иены", "иен")),
    (("лир", "try"), "TRY", ("лира", "лиры", "лир")),
    (("франк", "chf"), "CHF", ("франк", "франка", "франков")),
    (("лари", "gel"), "GEL", ("лари", "лари", "лари")),
    (("драм", "amd"), "AMD", ("драм", "драма", "драмов")),
)  # fmt: skip
FORMS = {code: forms for _, code, forms in CURRENCIES}

_REQUEST = re.compile(r"^(?P<n>\d+(?:[.,]\d+)?)?\s*(?P<src>.+?)\s+(?:в|во|на)\s+(?P<dst>.+)$")
_FILLER = re.compile(r"\b(сколько|будет|переведи|конвертируй|это|стоит)\b")


def find_currency(words: str) -> str | None:
    words = words.strip()
    for stems, code, _ in CURRENCIES:
        if any(words.startswith(s) or f" {s}" in f" {words}" for s in stems):
            return code
    return None


@dataclass(frozen=True)
class CurrencyRequest:
    amount: float
    src: str
    dst: str


def parse_currency(text: str) -> CurrencyRequest | None:
    norm = " ".join(_FILLER.sub(" ", normalize(text)).split())
    m = _REQUEST.match(norm)
    if not m:
        return None
    src, dst = find_currency(m.group("src")), find_currency(m.group("dst"))
    if src is None or dst is None or src == dst:
        return None
    amount = float((m.group("n") or "1").replace(",", "."))
    return CurrencyRequest(amount, src, dst)


class Rates:
    def __init__(self) -> None:
        self._rub_per_unit: dict[str, float] = {}
        self._fetched = 0.0

    async def get(self) -> dict[str, float]:
        if not self._rub_per_unit or time.time() - self._fetched > CACHE_SECONDS:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(RATES_URL)
                response.raise_for_status()
                data = response.json()
            rates = {"RUB": 1.0}
            for code, info in data["Valute"].items():
                rates[code] = float(info["Value"]) / float(info["Nominal"])
            self._rub_per_unit, self._fetched = rates, time.time()
        return self._rub_per_unit

    async def convert(self, req: CurrencyRequest) -> float:
        rates = await self.get()
        if req.src not in rates or req.dst not in rates:
            raise KeyError(req.src if req.src not in rates else req.dst)
        return req.amount * rates[req.src] / rates[req.dst]


def say_money(amount: float, code: str) -> str:
    rounded = round(amount, 2)
    number = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.2f}".replace(".", ",")
    return f"{number} {plural(rounded, FORMS[code])}"
