from __future__ import annotations


def safe_csv_cell(value: object) -> str:
    """Keep spreadsheet applications from interpreting exported text as formulas."""

    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text
