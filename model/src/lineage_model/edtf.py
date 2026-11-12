"""Claim dates.

A claim's `valid_edtf` is display text, "as the source said it". What the database reasons
with are four bounds: when the claimed state began (`start_earliest`..`start_latest`) and
when it ended (`end_earliest`..`end_latest`). These helpers make both from the phrasings
the sources actually give, so every writer collapses dates the same way.

For a status claim the dates say when the status began:

- `by("1937")`: shown on a sheet as of 1937, so it began by then (the map-date rule,
  I-0003) and still held at some point in 1937. Text `../1937`.
- `began("1931-05")`: a source says it began in May 1931. Text `1931-05/..`.
- `began_between("1955-06-30", "1960")`: one sheet shows it and a later one doesn't, so the
  change happened in between. Text `1955-06-30/1960`.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from pydantic import BaseModel

_DATE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _parts(edtf: str) -> tuple[int, int | None, int | None]:
    m = _DATE.match(edtf)
    if not m:
        raise ValueError(f"not a calendar date at year, month or day precision: {edtf!r}")
    y, mo, d = m.groups()
    return int(y), int(mo) if mo else None, int(d) if d else None


def first_day(edtf: str) -> dt.date:
    y, m, d = _parts(edtf)
    return dt.date(y, m or 1, d or 1)


def last_day(edtf: str) -> dt.date:
    y, m, d = _parts(edtf)
    if d:
        return dt.date(y, m, d)
    m = m or 12
    return dt.date(y, m, calendar.monthrange(y, m)[1])


class Valid(BaseModel):
    valid_edtf: str | None = None
    start_earliest: dt.date | None = None
    start_latest: dt.date | None = None
    end_earliest: dt.date | None = None
    end_latest: dt.date | None = None


def by(date: str) -> Valid:
    return Valid(valid_edtf=f"../{date}", start_latest=last_day(date), end_earliest=first_day(date))


def began(date: str) -> Valid:
    return Valid(valid_edtf=f"{date}/..", start_earliest=first_day(date), start_latest=last_day(date))


def began_between(after: str, before: str) -> Valid:
    return Valid(valid_edtf=f"{after}/{before}", start_earliest=first_day(after), start_latest=last_day(before))
