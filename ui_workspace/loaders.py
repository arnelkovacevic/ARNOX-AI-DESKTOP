"""Reads CSV and Excel files into pandas DataFrames for the table viewer."""

from __future__ import annotations

from pathlib import Path

import pandas

MAX_ROWS = 200_000


class TableLoadError(Exception):
    """A readable reason the table could not be loaded."""


def _read_csv(path: Path, separator: str | None) -> pandas.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return pandas.read_csv(
                path, sep=separator, engine="python", encoding=encoding,
                dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
        except pandas.errors.EmptyDataError:
            raise TableLoadError(f"{path.name} is empty.")
        except Exception as exc:
            raise TableLoadError(f"{path.name} could not be read as a table: {exc}")
    raise TableLoadError(f"{path.name} uses an unsupported text encoding: {last_error}")


def load_tables(path: str | Path) -> dict[str, pandas.DataFrame]:
    """Return {sheet name: DataFrame}. CSV and TSV give one sheet named after the file."""
    file = Path(path)
    ext = file.suffix.lower().lstrip(".")
    if ext in {"csv", "tsv"}:
        frame = _read_csv(file, "\t" if ext == "tsv" else None)
        return {file.stem: frame}
    if ext in {"xlsx", "xlsm", "xls"}:
        try:
            sheets = pandas.read_excel(file, sheet_name=None, dtype=str, keep_default_na=False)
        except Exception as exc:
            raise TableLoadError(f"{file.name} could not be opened: {exc}")
        if not sheets:
            raise TableLoadError(f"{file.name} has no sheets.")
        return {str(name): frame for name, frame in sheets.items()}
    raise TableLoadError(f"Unsupported table type: .{ext}")


def truncate(frame: pandas.DataFrame) -> tuple[pandas.DataFrame, bool]:
    if len(frame) > MAX_ROWS:
        return frame.head(MAX_ROWS), True
    return frame, False
