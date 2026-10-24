"""Value checks for every changeable field. Pure functions: no database, no I/O.

`check_value` returns the value in its canonical stored form, or raises Invalid.
The same checks run for a person's edit, an AI proposal, and an approver's
corrected value, so there is one definition of "a valid value".
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .rules import FieldRule

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?[+-]\d{2}:\d{2}$")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Invalid(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _int(rule: FieldRule, raw) -> int:
    # JSON true/false arrive as Python bool, which is a subclass of int: refuse them.
    if type(raw) is not int:
        raise Invalid("wrong_type", f"{rule.field} must be a whole number in {rule.unit}, got {raw!r}")
    return raw


def _text(rule: FieldRule, raw, max_len: int) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise Invalid("wrong_type", f"{rule.field} must be non-empty text")
    value = raw.strip()
    if len(value) > max_len:
        raise Invalid("too_long", f"{rule.field} is at most {max_len} characters, got {len(value)}")
    return value


def parse_local_time(raw, time_zone: str) -> datetime:
    """ISO 8601 with an explicit offset that matches the shop's zone at that instant."""
    if not isinstance(raw, str) or not ISO_WITH_OFFSET.match(raw):
        raise Invalid("bad_datetime", f"expected ISO 8601 with offset, e.g. 2026-10-23T16:40:00+01:00, got {raw!r}")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise Invalid("bad_datetime", f"{raw!r} is not a real date and time ({exc})")
    expected = dt.astimezone(ZoneInfo(time_zone)).utcoffset()
    if dt.utcoffset() != expected:
        hours = expected.total_seconds() / 3600
        raise Invalid("wrong_offset",
                      f"{raw} has the wrong offset: {time_zone} was UTC{hours:+.0f}:00 at that instant")
    return dt


def check_value(rule: FieldRule, raw, *, record: dict, time_zone: str, now: datetime | None = None):
    spec = rule.spec
    if rule.type == "datetime":
        dt = parse_local_time(raw, time_zone)
        hired = record.get("hired_at")
        if hired and dt < datetime.fromisoformat(hired):
            raise Invalid("before_hire", f"{raw} is before the hire started ({hired})")
        now = now or datetime.now(ZoneInfo(time_zone))
        if dt > now + timedelta(minutes=5):
            raise Invalid("in_future", f"{raw} is in the future")
        return dt.isoformat(timespec="seconds")

    if rule.type == "enum":
        if not isinstance(raw, str) or raw not in spec["values"]:
            raise Invalid("not_allowed_value", f"{rule.field} must be one of {spec['values']}, got {raw!r}")
        return raw

    if rule.type == "integer":
        value = _int(rule, raw)
        lo = spec.get("min", 0)
        hi = spec.get("max")
        if spec.get("max_rule") == "must not exceed rental.deposit_held_pence":
            hi = record["deposit_held_pence"]
        if value < lo or (hi is not None and value > hi):
            what = "the deposit held" if "max_rule" in spec else str(hi)
            raise Invalid("out_of_range", f"{rule.field} must be {lo} to {hi} {rule.unit} (max is {what}), got {value}")
        return value

    if rule.type == "text":
        return _text(rule, raw, spec.get("max_length", 500))

    if rule.type == "string":
        if rule.unit == "e164":
            if not isinstance(raw, str) or not E164.match(raw):
                raise Invalid("bad_phone", f"phone must be E.164, e.g. +447700900123, got {raw!r}")
            return raw
        if rule.unit == "email":
            value = _text(rule, raw, 254)
            if not EMAIL.match(value):
                raise Invalid("bad_email", f"not an email address: {raw!r}")
            return value
        value = _text(rule, raw, 200 if rule.column == "address" else 64)
        if "\n" in value or "\r" in value:
            raise Invalid("not_one_line", f"{rule.field} must be one line")
        return value

    raise Invalid("unsupported_field", f"no validator for {rule.field}")
