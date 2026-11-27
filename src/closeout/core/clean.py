"""Cleaning what a model read (design 3.2, R-IMP-5, R-SAFE-1). Pure.

Dates, money and license numbers are checked and cleaned by code, never trusted from the model.
Text fields lose links, email addresses, control characters and anything past 200 characters.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel

MONEY_MAX = Decimal(5_000_000)
TEXT_MAX = 200
LICENSE_RX = re.compile(r"^\d{2}VH\d{8}$")


def work_types() -> tuple[str, ...]:
    return (
        "deck",
        "roof",
        "kitchen",
        "bathroom",
        "addition",
        "windows",
        "hvac",
        "plumbing",
        "electrical",
        "other",
    )


class Cleaned(BaseModel):
    """One cleaned value, or None with the reason it was refused."""

    value: object | None
    flags: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None and not any(f.startswith("bad_") for f in self.flags)


def date_formats() -> tuple[str, ...]:
    return (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
        "%b. %d, %Y",
    )


def clean_date(raw: object, today: date) -> Cleaned:
    """A date between 1950-01-01 and today + 30 days, from many printed formats."""
    if raw is None or not str(raw).strip():
        return Cleaned(value=None, flags=("missing",))
    s = " ".join(str(raw).strip().split())
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    for fmt in date_formats():
        try:
            d = datetime.strptime(s, fmt).date()  # noqa: DTZ007 - a calendar date, no time zone
        except ValueError:
            continue
        if date(1950, 1, 1) <= d <= today + timedelta(days=30):
            return Cleaned(value=d)
        return Cleaned(value=None, flags=("bad_date_range",))
    return Cleaned(value=None, flags=("bad_date",))


def clean_money(raw: object) -> Cleaned:
    """An exact amount between 0 and 5,000,000, to the cent."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return Cleaned(value=None, flags=("missing",))
    if isinstance(raw, bool):
        return Cleaned(value=None, flags=("bad_money",))
    s = str(raw).strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        return Cleaned(value=None, flags=("bad_money",))
    if not d.is_finite() or d < 0 or d > MONEY_MAX:
        return Cleaned(value=None, flags=("bad_money_range",))
    return Cleaned(value=d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def clean_license(raw: object) -> Cleaned:
    """NJ home-improvement registration: two digits, VH, eight digits, after removing spaces and labels."""
    if raw is None or not str(raw).strip():
        return Cleaned(value=None, flags=("missing",))
    s = str(raw).upper()
    s = re.sub(r"(NJ|HIC|REG(ISTRATION)?|LIC(ENSE)?|NO|NUMBER|#|:|\.)", " ", s)
    s = re.sub(r"[\s-]", "", s)
    if LICENSE_RX.match(s):
        return Cleaned(value=s)
    return Cleaned(value=None, flags=("bad_license",))


def clean_work_type(raw: object) -> Cleaned:
    s = str(raw or "").strip().lower()
    if s in work_types():
        return Cleaned(value=s)
    return Cleaned(value=None, flags=("bad_work_type",) if s else ("missing",))


def clean_warranty(raw: object) -> Cleaned:
    """Years of warranty: a whole number 0..50, or none stated."""
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "none", "null")):
        return Cleaned(value=0, flags=("none_stated",))
    if isinstance(raw, bool):
        return Cleaned(value=None, flags=("bad_warranty",))
    try:
        n = Decimal(str(raw).strip())
    except InvalidOperation:
        return Cleaned(value=None, flags=("bad_warranty",))
    if n != n.to_integral_value() or not 0 <= n <= 50:
        return Cleaned(value=None, flags=("bad_warranty",))
    return Cleaned(value=int(n))


def link_tlds() -> str:
    return (
        "com|net|org|edu|gov|example|test|io|co|dev|app|xyz|info|biz|us|me|ly|gg|ai|link|site|online|top|"
        "page|click|live|shop|store|pay|cash|money|help|support|tech|cloud|email|ws|tk|ru|cn|uk"
    )


# Any scheme (http, ftp, javascript, data...), www., or a domain-shaped word with a known ending.
URL_RX = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*:/{1,3}\S*|\bwww\.\S+|\b(?:javascript|data|mailto|file):\S+|"
    rf"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{link_tlds()})(?:[/?#:]\S*)?\b"
)
EMAIL_RX = re.compile(r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")


def clean_text(raw: object) -> Cleaned:
    """Free text: no links, email addresses or control characters, at most 200 characters."""
    if raw is None or not str(raw).strip():
        return Cleaned(value=None, flags=("missing",))
    s = str(raw)
    flags: list[str] = []
    no_ctrl = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" or ch == " ")
    if no_ctrl != s:
        flags.append("control_chars_removed")
    s = no_ctrl
    if EMAIL_RX.search(s):
        s = EMAIL_RX.sub(" ", s)
        flags.append("email_removed")
    if URL_RX.search(s):
        s = URL_RX.sub(" ", s)
        flags.append("link_removed")
    s = " ".join(s.split())
    if len(s) > TEXT_MAX:
        s = s[:TEXT_MAX].rstrip()
        flags.append("truncated")
    if not s:
        return Cleaned(value=None, flags=(*flags, "bad_empty_after_cleanup"))
    return Cleaned(value=s, flags=tuple(flags))


def clean_email(raw: object) -> Cleaned:
    """An email read from a document. Used only to compare with our contractor list, never to send to."""
    s = str(raw or "").strip().lower()
    if not s:
        return Cleaned(value=None, flags=("missing",))
    if re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", s) and len(s) <= TEXT_MAX:
        return Cleaned(value=s)
    return Cleaned(value=None, flags=("bad_email",))


def invoice_fields() -> tuple[str, ...]:
    return (
        "contractor_name",
        "contractor_license",
        "contractor_email",
        "customer_name",
        "job_address",
        "work_type",
        "completion_date",
        "total_amount",
        "warranty_years",
    )


def clean_field(field: str, raw: object, today: date) -> Cleaned:
    if field == "completion_date":
        return clean_date(raw, today)
    if field == "total_amount":
        return clean_money(raw)
    if field == "contractor_license":
        return clean_license(raw)
    if field == "work_type":
        return clean_work_type(raw)
    if field == "warranty_years":
        return clean_warranty(raw)
    if field == "contractor_email":
        return clean_email(raw)
    if field in ("contractor_name", "customer_name", "job_address"):
        return clean_text(raw)
    raise ValueError(f"unknown field {field}")


def clean_read(output: dict[str, object] | None, today: date) -> dict[str, Cleaned]:
    if not output:
        return {f: Cleaned(value=None, flags=("bad_read",)) for f in invoice_fields()}
    return {f: clean_field(f, output.get(f), today) for f in invoice_fields()}
