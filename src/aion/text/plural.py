"""Russian plural forms."""

from __future__ import annotations


def plural(n: float, forms: tuple[str, str, str]) -> str:
    """Pick a form for ``n``: ``plural(5, ("минута", "минуты", "минут")) -> "минут"``."""
    if n != int(n):
        return forms[1]
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def fmt_count(n: float, forms: tuple[str, str, str]) -> str:
    """``fmt_count(3, ("минута", "минуты", "минут")) -> "3 минуты"``."""
    number = str(int(n)) if n == int(n) else f"{n:g}".replace(".", ",")
    return f"{number} {plural(n, forms)}"
