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


def parse_quantity(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)", str(value))
    if not match:
        raise ValueError("syntax")
    number, unit = float(match[1]), match[2]
    if unit not in BASIC_UNITS:
        raise ValueError("unit")
    return number * BASIC_UNITS[unit]
