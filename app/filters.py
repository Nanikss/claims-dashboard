"""Parse and validate query-string input before it gets anywhere near SQL."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping

STATES = ("CA", "NY", "TX", "FL")
LINES = ("Commercial Property", "General Liability", "Commercial Auto")

MAX_PER_PAGE = 100


class ValidationError(ValueError):
    """Bad client input; rendered as a 400 with a JSON error body."""


@dataclass(frozen=True)
class Filters:
    state: str | None = None
    line: str | None = None
    date_from: str | None = None  # ISO date, inclusive, applied to loss_date
    date_to: str | None = None    # ISO date, inclusive, applied to loss_date

    def params(self) -> dict[str, str | None]:
        """Named SQL parameters. Every key is always present (NULL = no filter)."""
        return {"state": self.state, "line": self.line,
                "date_from": self.date_from, "date_to": self.date_to}


def _choice(args: Mapping[str, str], name: str, allowed: tuple[str, ...]) -> str | None:
    value = args.get(name, "").strip()
    if not value:
        return None
    if value not in allowed:
        raise ValidationError(f"unknown {name} {value!r}; expected one of {', '.join(allowed)}")
    return value


def _iso_date(args: Mapping[str, str], name: str) -> str | None:
    value = args.get(name, "").strip()
    if not value:
        return None
    try:
        # Strict YYYY-MM-DD; fromisoformat alone also accepts "20250101" and "2025-W01-1".
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValidationError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def parse_filters(args: Mapping[str, str]) -> Filters:
    filters = Filters(
        state=_choice(args, "state", STATES),
        line=_choice(args, "line", LINES),
        date_from=_iso_date(args, "from"),
        date_to=_iso_date(args, "to"),
    )
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ValidationError("'from' must be on or before 'to'")
    return filters


def parse_int(args: Mapping[str, str], name: str, default: int, lo: int, hi: int | None = None) -> int:
    raw = args.get(name, "").strip()
    if not raw:
        return default
    if not re.fullmatch(r"-?[0-9]{1,9}", raw):
        raise ValidationError(f"{name} must be an integer, got {raw!r}")
    value = int(raw)
    if value < lo or (hi is not None and value > hi):
        bounds = f">= {lo}" if hi is None else f"between {lo} and {hi}"
        raise ValidationError(f"{name} must be {bounds}, got {value}")
    return value
