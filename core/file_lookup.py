"""Finds a file from a spoken or typed name, tolerant of dashes, accents, case and missing extensions.

A voice assistant rarely reproduces a file name character for character: "Arnel Kovacevic — CV.pdf"
may arrive with a plain hyphen, without the extension, or only as "curriculum vitae".
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_KNOWN_EXTS = {
    "pdf", "csv", "tsv", "xlsx", "xlsm", "xls", "docx", "doc", "txt", "md", "json", "html", "pptx",
    "png", "jpg", "jpeg", "mp3", "mp4", "zip",
}


def default_roots() -> list[Path]:
    home = Path.home()
    return [home / "Desktop", home / "Downloads", home / "Documents"]


def normalize(text: str) -> str:
    """Lowercase, strip accents, turn every dash and separator into a space."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]|_", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _split_extension(query: str) -> tuple[str, str]:
    cleaned = str(query or "").strip().strip("\"'")
    suffix = Path(cleaned).suffix.lower().lstrip(".")
    if suffix in _KNOWN_EXTS:
        return cleaned[: -(len(suffix) + 1)], suffix
    # Spoken extension: "report pdf" arrives as a trailing word, not ".pdf".
    words = cleaned.split()
    if len(words) > 1 and words[-1].lower() in _KNOWN_EXTS:
        return " ".join(words[:-1]), words[-1].lower()
    return cleaned, ""


def find_file(query: str, roots: list[Path] | None = None) -> tuple[Path | None, list[Path]]:
    """Return (path, []) for one clear match, (None, candidates) when several fit, (None, []) otherwise."""
    name, extension = _split_extension(query)
    wanted = normalize(name)
    if not wanted:
        return None, []
    tokens = wanted.split()
    exact: list[Path] = []
    partial: list[Path] = []
    for root in roots if roots is not None else default_roots():
        try:
            children = [child for child in root.iterdir() if child.is_file() and not child.name.startswith(".")]
        except OSError:
            continue
        for child in children:
            if extension and child.suffix.lower().lstrip(".") != extension:
                continue
            stem = normalize(child.stem)
            if stem == wanted:
                exact.append(child)
            elif all(token in stem.split() or token in stem for token in tokens):
                partial.append(child)
    matches = exact or partial
    if len(matches) == 1:
        return matches[0], []
    return None, sorted(matches, key=lambda p: p.name.lower())[:6]
