"""Parsing of platform posting-date strings + freshness filter.

Supported formats (verified against live data):
  - gov.hk:            DD/MM/YYYY            (e.g. "11/08/2026")
  - JobsDB / SEEK:     relative "N{d,h,w,mo,y} ago", "30+ days ago",
                       "Today", "Yesterday", possibly with trailing
                       decoration like "24d ago\n•\nExpiring"
  - OfferToday:        no posting date exposed -> empty -> kept (unknown age)
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_RELATIVE = re.compile(
    r"(\d+)\s*(h|hr|hrs|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|year|years)\s+ago",
    re.IGNORECASE,
)
_PLUS_DAYS = re.compile(r"(\d+)\+\s*days?\s*ago", re.IGNORECASE)
_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")

_UNIT_DAYS = {"h": 0, "d": 1, "w": 7, "mo": 30, "y": 365}


def parse_posted_date(text: str) -> date | None:
    """Parse a posting-date string into a ``date``; None when unparseable."""
    if not text:
        return None
    s = text.strip()
    low = s.lower()
    today = date.today()

    if re.search(r"\btoday\b", low):
        return today
    if re.search(r"\byesterday\b", low):
        return today - timedelta(days=1)

    m = _RELATIVE.search(low)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        # hours ago -> same day; days/weeks/months/years -> n * unit days
        days = n * _UNIT_DAYS.get(unit[0], 0)
        if unit[0] == "m":
            days = n * 30
        return today - timedelta(days=days)

    m = _PLUS_DAYS.search(low)
    if m:
        # "30+ days ago" -> at least 30 days old; treat as exactly 30 (minimum)
        return today - timedelta(days=int(m.group(1)))

    m = _DMY.match(s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    m = _YMD.match(s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    return None


def is_fresh(posted_at: str, max_age_days: int) -> bool:
    """True when the posting date is within ``max_age_days`` of today.

    Unparseable / empty dates are kept (we cannot judge age — e.g. OfferToday
    exposes no posting date, dropping everything would starve the board).
    """
    parsed = parse_posted_date(posted_at)
    if parsed is None:
        return True
    return (date.today() - parsed).days <= max_age_days
