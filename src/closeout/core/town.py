"""Reading a town clerk's reply (R-TOWN-2). Pure.

A result is shown only if both reads agree on it and on its date, and the letter is about the permit
we asked about. Anything else is "unclear" and goes to ops. The homeowner never settles it.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

from closeout.core.clean import clean_date

RESULTS = ("final_on_file", "no_record", "unclear")


class TownResult(BaseModel):
    result: str  # final_on_file | no_record | unclear
    result_date: date | None
    reason: str  # agree | disagree | invalid | wrong_permit | read_unclear | no_date


PERMIT_TOKEN = re.compile(r"(?<![0-9A-Za-z-])(\d{2})\s*[-\u2013 ]\s*(\d{3,5})(?![0-9])")


def permit_key(s: object) -> str:
    """Permit numbers compared by their digits, so "Permit #22 0147" is 22-0147."""
    return re.sub(r"[^0-9]", "", str(s or ""))


def same_permit(field: object, permit_no: str) -> bool:
    """A read's permit field names the asked permit and no other one.

    Readers add words around the number ("22-0147 (furnace and AC replacement)", "OPRA Request W-2026-0859 -
    Permit No. 24-0648"), so only permit-shaped tokens count. With no such token, all its digits are compared.
    """
    tokens = {a + b for a, b in PERMIT_TOKEN.findall(str(field or ""))}
    if tokens:
        return tokens == {permit_key(permit_no)}
    return permit_key(field) == permit_key(permit_no)


def one_read(read: dict[str, object] | None, permit_no: str, today: date) -> TownResult | None:
    if not read or read.get("result") not in RESULTS:
        return None
    if read.get("asks_for_more_information") is True:
        return TownResult(result="unclear", result_date=None, reason="more_info")
    if read.get("work_still_outstanding") is True:  # a partial approval is not a final one
        return TownResult(result="unclear", result_date=None, reason="outstanding")
    answer_about = read.get("result_permit_number")
    if answer_about not in (None, "") and not same_permit(answer_about, permit_no):
        return TownResult(
            result="unclear", result_date=None, reason="wrong_permit"
        )  # an answer about another permit
    if not same_permit(read.get("permit_number"), permit_no):
        return TownResult(result="unclear", result_date=None, reason="wrong_permit")
    if read["result"] == "final_on_file":
        d = clean_date(read.get("result_date"), today)
        if not d.ok:
            return TownResult(result="unclear", result_date=None, reason="no_date")
        return TownResult(result="final_on_file", result_date=d.value, reason="agree")  # type: ignore[arg-type]
    return TownResult(result=str(read["result"]), result_date=None, reason="agree")


def decide(
    read_a: dict[str, object] | None, read_b: dict[str, object] | None, permit_no: str, today: date
) -> TownResult:
    a, b = one_read(read_a, permit_no, today), one_read(read_b, permit_no, today)
    if a is None or b is None:
        return TownResult(result="unclear", result_date=None, reason="invalid")
    for r in (a, b):
        if r.reason != "agree":
            return TownResult(result="unclear", result_date=None, reason=r.reason)
    if a.result == "unclear" or b.result == "unclear":
        return TownResult(result="unclear", result_date=None, reason="read_unclear")
    if (a.result, a.result_date) != (b.result, b.result_date):
        return TownResult(result="unclear", result_date=None, reason="disagree")
    return a
