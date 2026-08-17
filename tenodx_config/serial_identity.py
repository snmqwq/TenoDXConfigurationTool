"""Helpers for identifying TenoDX USB serial functions."""

from __future__ import annotations

from typing import Any


def matches_tenodx_serial_function(
    port: Any, bus_description: str | None, function_name: str
) -> bool:
    """Return whether the reported USB identity names one TenoDX function."""

    identity = " ".join(
        str(value)
        for value in (
            bus_description,
            getattr(port, "description", ""),
            getattr(port, "interface", ""),
            getattr(port, "product", ""),
        )
        if value
    ).casefold()
    return "tenodx" in identity and function_name.casefold() in identity
