from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import SupportsFloat


def to_decimal(value: object, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return default
    text = str(value).strip()
    if not text or text in {"-", "—", "NA", "N/A", "nan", "None"}:
        return default
    cleaned = (
        text.replace("₹", "")
        .replace(",", "")
        .replace("%", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("INR", "")
        .strip()
    )
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return default


def quantize(value: Decimal, places: int = 2) -> Decimal:
    step = Decimal("1").scaleb(-places)
    return value.quantize(step, rounding=ROUND_HALF_UP)


def indian_comma(value: Decimal | float | int, places: int = 2) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    number = quantize(number, places)
    sign = "-" if number < 0 else ""
    number = abs(number)
    raw = f"{number:.{places}f}"
    if places == 0:
        whole, frac = raw, None
    else:
        whole, frac = raw.split(".")
    grouped = _group_indian(whole)
    if frac is None:
        return f"{sign}{grouped}"
    return f"{sign}{grouped}.{frac}"


def _group_indian(whole: str) -> str:
    if len(whole) <= 3:
        return whole
    last = whole[-3:]
    rest = whole[:-3]
    chunks: list[str] = []
    while rest:
        chunks.append(rest[-2:])
        rest = rest[:-2]
    return ",".join(reversed(chunks)) + "," + last


def format_inr(
    value: Decimal | float | int | None,
    *,
    compact: bool = False,
    signed: bool = False,
    places: int = 2,
) -> str:
    if value is None:
        return "—"
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if compact:
        body = _compact(abs(number))
    else:
        body = f"₹{indian_comma(abs(number), places)}"
    if number < 0:
        return f"-{body}"
    if signed and number > 0:
        return f"+{body}"
    return body


def _compact(value: Decimal) -> str:
    abs_val = abs(value)
    if abs_val >= Decimal("10000000"):
        cr = abs_val / Decimal("10000000")
        return f"₹{indian_comma(cr, 2)} Cr"
    if abs_val >= Decimal("100000"):
        lakhs = abs_val / Decimal("100000")
        return f"₹{indian_comma(lakhs, 2)} L"
    return f"₹{indian_comma(abs_val, 2)}"


def format_pct(value: Decimal | float | int | None, *, signed: bool = True, places: int = 2) -> str:
    if value is None:
        return "—"
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    formatted = indian_comma(abs(number), places)
    if number < 0:
        return f"-{formatted}%"
    if signed and number > 0:
        return f"+{formatted}%"
    return f"{formatted}%"


def format_qty(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if number == number.to_integral_value():
        return indian_comma(number, 0)
    return indian_comma(number, 2)


def as_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def safe_div(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def coerce_number(value: SupportsFloat | Decimal | None) -> float:
    if value is None:
        return 0.0
    return float(value)
