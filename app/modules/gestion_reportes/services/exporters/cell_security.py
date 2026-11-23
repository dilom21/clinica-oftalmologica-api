"""Neutralización común de fórmulas para exportaciones tabulares."""

import re


_FORMULA_PREFIX = re.compile(r"^[\x00-\x20]*[=+\-@]")


def safe_spreadsheet_value(value):
    if not isinstance(value, str):
        return value
    limpio = value.replace("\x00", "")
    if _FORMULA_PREFIX.match(limpio):
        return "'" + limpio
    return limpio
