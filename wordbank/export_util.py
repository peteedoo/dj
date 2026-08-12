import re
from pathlib import Path


INVALID_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
WHITESPACE = re.compile(r"\s+")
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_BASENAME = 80


def sanitize_label_filename(label: str, fallback: str = "sample") -> str:
    """Turn a sample label into a DJ-folder-safe basename (no extension)."""
    cleaned = WHITESPACE.sub("_", str(label).strip())
    cleaned = INVALID_FILENAME.sub("", cleaned)
    cleaned = cleaned.strip("._") or fallback
    if cleaned.upper() in RESERVED:
        cleaned = f"{cleaned}_sample"
    return cleaned[:MAX_BASENAME]


def unique_path(directory: Path, basename: str, suffix: str = ".wav") -> Path:
    """Return a path that can be created exclusively (retry on collision)."""
    directory.mkdir(parents=True, exist_ok=True)
    counter = 1
    while True:
        name = basename if counter == 1 else f"{basename}_{counter}"
        candidate = directory / f"{name}{suffix}"
        try:
            fd = candidate.open("x")
            fd.close()
            return candidate
        except FileExistsError:
            counter += 1


def escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def normalize_tags(tags: str) -> str:
    parts = [part.strip() for part in str(tags).split(",")]
    return ",".join(part for part in parts if part)


def normalize_speaker(speaker: str | None) -> str | None:
    if speaker is None:
        return None
    cleaned = " ".join(str(speaker).split())
    return cleaned.casefold() or None
