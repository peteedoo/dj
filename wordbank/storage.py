import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .export_util import escape_like, normalize_speaker, normalize_tags
from .models import Word

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    audio_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    speaker TEXT,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    transcript TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    raw_word TEXT NOT NULL,
    normalized TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    confidence REAL
);
CREATE INDEX IF NOT EXISTS words_normalized_clip ON words(normalized, clip_id);
CREATE INDEX IF NOT EXISTS clips_speaker ON clips(speaker);
CREATE INDEX IF NOT EXISTS words_clip_position ON words(clip_id, position);
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    start_word_index INTEGER NOT NULL,
    end_word_index INTEGER NOT NULL,
    label TEXT,
    text TEXT NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    pad_before REAL NOT NULL,
    pad_after REAL NOT NULL,
    exported_path TEXT NOT NULL,
    published_path TEXT,
    speaker TEXT,
    tags TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(word: str) -> str:
    return "".join(
        character.lower()
        for character in word
        if character.isalnum() or character == "'"
    ).strip("'")


class Store:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "wordbank.sqlite3"
        self.audio_dir = self.data_dir / "audio"
        self.sample_dir = self.data_dir / "samples"
        self.tmp_dir = self.data_dir / ".tmp"
        self.audio_dir.mkdir(exist_ok=True)
        self.sample_dir.mkdir(exist_ok=True)
        self.tmp_dir.mkdir(exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def init(self) -> None:
        with self.connect() as database:
            database.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in database.execute("PRAGMA table_info(samples)")
            }
            if "published_path" not in columns:
                database.execute(
                    "ALTER TABLE samples ADD COLUMN published_path TEXT"
                )

    def clip(self, clip_id: int) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute(
                "SELECT * FROM clips WHERE id=?", (clip_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            words = database.execute(
                "SELECT * FROM words WHERE clip_id=? ORDER BY position",
                (clip_id,),
            )
            result["words"] = [dict(word) for word in words]
            return result

    def clips(self) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                "SELECT * FROM clips ORDER BY created_at DESC"
            )
            return [dict(row) for row in rows]

    def add_clip(
        self,
        path: Path,
        original: str,
        speaker: str | None,
        clip_duration: float,
        words: Iterable[Word],
    ) -> int:
        word_list = []
        for word in words:
            text = word.text.strip()
            if not text or not normalize(text):
                continue
            if word.end <= word.start:
                continue
            word_list.append(
                Word(
                    text=text,
                    start=word.start,
                    end=word.end,
                    confidence=word.confidence,
                )
            )
        transcript = " ".join(word.text for word in word_list)
        speaker = normalize_speaker(speaker)
        with self.connect() as database:
            cursor = database.execute(
                """
                INSERT INTO clips(
                    audio_path, original_filename, speaker, duration,
                    created_at, transcript
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(path), original, speaker, clip_duration, now(), transcript),
            )
            clip_id = int(cursor.lastrowid)
            database.executemany(
                """
                INSERT INTO words(
                    clip_id, position, raw_word, normalized,
                    start, end, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        clip_id,
                        position,
                        word.text,
                        normalize(word.text),
                        word.start,
                        word.end,
                        word.confidence,
                    )
                    for position, word in enumerate(word_list)
                ],
            )
        return clip_id

    def delete_clip(self, clip_id: int) -> bool:
        clip = self.clip(clip_id)
        if clip is None:
            return False
        samples = self.samples_for_clip(clip_id)
        with self.connect() as database:
            database.execute("DELETE FROM clips WHERE id=?", (clip_id,))
        Path(clip["audio_path"]).unlink(missing_ok=True)
        Path(clip["audio_path"]).with_suffix(".json").unlink(missing_ok=True)
        for sample in samples:
            Path(sample["exported_path"]).unlink(missing_ok=True)
            published = sample.get("published_path")
            if published:
                Path(published).unlink(missing_ok=True)
        return True

    def samples_for_clip(self, clip_id: int) -> list[dict[str, Any]]:
        with self.connect() as database:
            rows = database.execute(
                "SELECT * FROM samples WHERE clip_id=?", (clip_id,)
            )
            return [dict(row) for row in rows]

    def search(
        self,
        query: str,
        speaker: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        tokens = [normalize(token) for token in query.split()]
        tokens = [token for token in tokens if token]
        if not tokens:
            return []
        limit = max(1, min(int(limit), 500))
        speaker = normalize_speaker(speaker)
        with self.connect() as database:
            parameters: list[Any] = [tokens[0]]
            sql = """
                SELECT words.*, clips.speaker, clips.original_filename
                FROM words
                JOIN clips ON clips.id=words.clip_id
                WHERE words.normalized=?
            """
            if speaker:
                sql += " AND clips.speaker=?"
                parameters.append(speaker)
            # Scan all first-token matches; limit applies to finished phrase hits.
            sql += " ORDER BY words.clip_id, words.position"
            candidates = database.execute(sql, parameters).fetchall()
            hits: list[dict[str, Any]] = []
            for first in candidates:
                rows = database.execute(
                    """
                    SELECT * FROM words
                    WHERE clip_id=? AND position BETWEEN ? AND ?
                    ORDER BY position
                    """,
                    (
                        first["clip_id"],
                        first["position"],
                        first["position"] + len(tokens) - 1,
                    ),
                ).fetchall()
                if [row["normalized"] for row in rows] != tokens:
                    continue
                before = database.execute(
                    """
                    SELECT raw_word FROM words
                    WHERE clip_id=? AND position<?
                    ORDER BY position DESC LIMIT 3
                    """,
                    (first["clip_id"], first["position"]),
                ).fetchall()
                after = database.execute(
                    """
                    SELECT raw_word FROM words
                    WHERE clip_id=? AND position>?
                    ORDER BY position LIMIT 3
                    """,
                    (first["clip_id"], rows[-1]["position"]),
                ).fetchall()
                hits.append(
                    {
                        "clip_id": first["clip_id"],
                        "speaker": first["speaker"],
                        "filename": first["original_filename"],
                        "text": " ".join(row["raw_word"] for row in rows),
                        "start": rows[0]["start"],
                        "end": rows[-1]["end"],
                        "word_start": rows[0]["position"],
                        "word_end": rows[-1]["position"],
                        "context_before": [
                            row["raw_word"] for row in reversed(before)
                        ],
                        "context_after": [row["raw_word"] for row in after],
                    }
                )
                if len(hits) >= limit:
                    break
            return hits

    def add_sample(self, **values: Any) -> int:
        values.setdefault("created_at", now())
        values.setdefault("tags", "")
        values.setdefault("published_path", None)
        values["tags"] = normalize_tags(values.get("tags") or "")
        values["speaker"] = normalize_speaker(values.get("speaker"))
        fields = [
            "clip_id",
            "start_word_index",
            "end_word_index",
            "label",
            "text",
            "start_seconds",
            "end_seconds",
            "pad_before",
            "pad_after",
            "exported_path",
            "published_path",
            "speaker",
            "tags",
            "created_at",
        ]
        placeholders = ", ".join("?" for _ in fields)
        columns = ", ".join(fields)
        with self.connect() as database:
            cursor = database.execute(
                f"INSERT INTO samples({columns}) VALUES ({placeholders})",
                [values.get(field) for field in fields],
            )
            return int(cursor.lastrowid)

    def samples(
        self,
        query: str | None = None,
        speaker: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as database:
            sql = "SELECT * FROM samples WHERE 1=1"
            parameters: list[Any] = []
            if query:
                sql += " AND text LIKE ? ESCAPE '\\'"
                parameters.append(f"%{escape_like(query)}%")
            if speaker:
                sql += " AND speaker=?"
                parameters.append(normalize_speaker(speaker))
            if tag:
                sql += " AND (',' || tags || ',') LIKE ? ESCAPE '\\'"
                parameters.append(f"%,{escape_like(tag.strip())},%")
            sql += " ORDER BY created_at DESC"
            rows = database.execute(sql, parameters)
            return [dict(row) for row in rows]

    def sample(self, sample_id: int) -> dict[str, Any] | None:
        with self.connect() as database:
            row = database.execute(
                "SELECT * FROM samples WHERE id=?", (sample_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def update_sample(self, sample_id: int, **values: Any) -> dict[str, Any] | None:
        allowed = {
            "label",
            "text",
            "start_seconds",
            "end_seconds",
            "pad_before",
            "pad_after",
            "exported_path",
            "published_path",
            "tags",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if "tags" in updates and updates["tags"] is not None:
            updates["tags"] = normalize_tags(updates["tags"])
        if not updates:
            return self.sample(sample_id)
        assignments = ", ".join(f"{field}=?" for field in updates)
        with self.connect() as database:
            cursor = database.execute(
                f"UPDATE samples SET {assignments} WHERE id=?",
                [*updates.values(), sample_id],
            )
            if cursor.rowcount == 0:
                return None
        return self.sample(sample_id)

    def delete_sample(self, sample_id: int) -> bool:
        sample = self.sample(sample_id)
        if sample is None:
            return False
        with self.connect() as database:
            database.execute("DELETE FROM samples WHERE id=?", (sample_id,))
        Path(sample["exported_path"]).unlink(missing_ok=True)
        published = sample.get("published_path")
        if published:
            Path(published).unlink(missing_ok=True)
        return True
