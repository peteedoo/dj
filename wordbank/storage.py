import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
 id INTEGER PRIMARY KEY, audio_path TEXT NOT NULL, original_filename TEXT NOT NULL,
 speaker TEXT, duration REAL NOT NULL, created_at TEXT NOT NULL, transcript TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS words (
 id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, raw_word TEXT NOT NULL, normalized TEXT NOT NULL,
 start REAL NOT NULL, end REAL NOT NULL, confidence REAL
);
CREATE INDEX IF NOT EXISTS words_normalized_speaker ON words(normalized, clip_id);
CREATE INDEX IF NOT EXISTS words_clip_position ON words(clip_id, position);
CREATE TABLE IF NOT EXISTS samples (
 id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
 start_word_index INTEGER NOT NULL, end_word_index INTEGER NOT NULL, label TEXT,
 text TEXT NOT NULL, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
 pad_before REAL NOT NULL, pad_after REAL NOT NULL, exported_path TEXT NOT NULL,
 speaker TEXT, tags TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: str | Path = "wordbank/data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "wordbank.sqlite3"
        self.audio_dir = self.data_dir / "audio"
        self.sample_dir = self.data_dir / "samples"
        self.audio_dir.mkdir(exist_ok=True)
        self.sample_dir.mkdir(exist_ok=True)
        self.init()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self):
        with self.connect() as db:
            db.executescript(SCHEMA)

    def clip(self, clip_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["words"] = [dict(w) for w in db.execute(
                "SELECT * FROM words WHERE clip_id=? ORDER BY position", (clip_id,))]
            return result

    def clips(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM clips ORDER BY created_at DESC")]

    def add_clip(self, path, original, speaker, duration, words):
        transcript = " ".join(w.text for w in words)
        with self.connect() as db:
            cur = db.execute("INSERT INTO clips(audio_path,original_filename,speaker,duration,created_at,transcript) VALUES (?,?,?,?,?,?)",
                             (str(path), original, speaker, duration, now(), transcript))
            clip_id = cur.lastrowid
            db.executemany("INSERT INTO words(clip_id,position,raw_word,normalized,start,end,confidence) VALUES (?,?,?,?,?,?,?)",
                           [(clip_id, i, w.text, normalize(w.text), w.start, w.end, w.confidence)
                            for i, w in enumerate(words)])
        return clip_id

    def search(self, query, speaker=None):
        tokens = [normalize(x) for x in query.split() if normalize(x)]
        if not tokens:
            return []
        with self.connect() as db:
            params = [tokens[0]]
            sql = "SELECT w.*, c.speaker, c.original_filename FROM words w JOIN clips c ON c.id=w.clip_id WHERE w.normalized=?"
            if speaker:
                sql += " AND c.speaker=?"
                params.append(speaker)
            candidates = db.execute(sql, params).fetchall()
            hits = []
            for first in candidates:
                rows = db.execute("SELECT * FROM words WHERE clip_id=? AND position BETWEEN ? AND ? ORDER BY position",
                                  (first["clip_id"], first["position"], first["position"] + len(tokens) - 1)).fetchall()
                if [r["normalized"] for r in rows] != tokens:
                    continue
                before = db.execute("SELECT raw_word FROM words WHERE clip_id=? AND position<? ORDER BY position DESC LIMIT 3",
                                    (first["clip_id"], first["position"])).fetchall()
                after = db.execute("SELECT raw_word FROM words WHERE clip_id=? AND position>? ORDER BY position LIMIT 3",
                                   (first["clip_id"], rows[-1]["position"])).fetchall()
                hits.append({"clip_id": first["clip_id"], "speaker": first["speaker"],
                             "filename": first["original_filename"], "text": " ".join(r["raw_word"] for r in rows),
                             "start": rows[0]["start"], "end": rows[-1]["end"],
                             "word_start": rows[0]["position"], "word_end": rows[-1]["position"],
                             "context_before": [r["raw_word"] for r in reversed(before)],
                             "context_after": [r["raw_word"] for r in after]})
            return hits

    def add_sample(self, **values):
        values.setdefault("created_at", now())
        values.setdefault("tags", "")
        fields = ["clip_id", "start_word_index", "end_word_index", "label", "text", "start_seconds", "end_seconds",
                  "pad_before", "pad_after", "exported_path", "speaker", "tags", "created_at"]
        with self.connect() as db:
            cur = db.execute(f"INSERT INTO samples({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                             [values.get(f) for f in fields])
            return cur.lastrowid

    def samples(self, query=None, speaker=None, tag=None):
        with self.connect() as db:
            sql, args = "SELECT * FROM samples WHERE 1=1", []
            if query:
                sql += " AND text LIKE ?"; args.append(f"%{query}%")
            if speaker:
                sql += " AND speaker=?"; args.append(speaker)
            if tag:
                sql += " AND (','||tags||',') LIKE ?"; args.append(f"%,{tag},%")
            return [dict(r) for r in db.execute(sql + " ORDER BY created_at DESC", args)]

    def sample(self, sample_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
            return dict(row) if row else None

    def delete_sample(self, sample_id):
        with self.connect() as db:
            row = db.execute("SELECT exported_path FROM samples WHERE id=?", (sample_id,)).fetchone()
            if row:
                Path(row["exported_path"]).unlink(missing_ok=True)
                db.execute("DELETE FROM samples WHERE id=?", (sample_id,))
            return bool(row)


def normalize(word: str) -> str:
    return "".join(ch.lower() for ch in word if ch.isalnum() or ch == "'").strip("'")
