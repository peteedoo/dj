import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
    def __init__(self, data_dir: str | Path = "wordbank/data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "wordbank.sqlite3"
        self.audio_dir = self.data_dir / "audio"
        self.sample_dir = self.data_dir / "samples"
        self.audio_dir.mkdir(exist_ok=True)
        self.sample_dir.mkdir(exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init(self) -> None:
        with self.connect() as database:
            database.executescript(SCHEMA)

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
        word_list = list(words)
        transcript = " ".join(word.text for word in word_list)
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
        limit = max(1, limit)
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
            sql += " ORDER BY words.clip_id, words.position LIMIT ?"
            parameters.append(limit)
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
            return hits

    def add_sample(self, **values: Any) -> int:
        values.setdefault("created_at", now())
        values.setdefault("tags", "")
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
                sql += " AND text LIKE ?"
                parameters.append(f"%{query}%")
            if speaker:
                sql += " AND speaker=?"
                parameters.append(speaker)
            if tag:
                sql += " AND (',' || tags || ',') LIKE ?"
                parameters.append(f"%,{tag},%")
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

    def delete_sample(self, sample_id: int) -> bool:
        with self.connect() as database:
            row = database.execute(
                "SELECT exported_path FROM samples WHERE id=?", (sample_id,)
            ).fetchone()
            if row is None:
                return False
            Path(row["exported_path"]).unlink(missing_ok=True)
            database.execute("DELETE FROM samples WHERE id=?", (sample_id,))
            return True
