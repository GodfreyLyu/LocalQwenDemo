"""Kubernetes quantity syntax; aggregation policies belong to their callers."""

import re

BASIC_UNITS = {
    "": 1,
    "m": 0.001,
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
}
EXTENDED_UNITS = BASIC_UNITS | {"n": 1e-9, "u": 1e-6, "k": 1e3, "T": 1e12}


def parse_quantity(value, *, extended=False):
    if extended and not value:
        return 0.0
    suffix = r"([a-zA-Z]*|[eE][+-]?\d+)" if extended else r"([A-Za-z]*)"
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)" + suffix, str(value))
    if not match:
        raise ValueError("syntax")
    number, unit = float(match[1]), match[2]
    if extended and unit.startswith(("e", "E")):
        return number * 10 ** int(unit[1:])
    units = EXTENDED_UNITS if extended else BASIC_UNITS
    if unit not in units:
        raise ValueError("unit")
    return number * units[unit]
