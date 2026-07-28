import json
import os
from pathlib import Path
from typing import Protocol, Iterable

from .models import Word


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> Iterable[Word]: ...


class JSONTranscriber:
    def transcribe(self, audio_path: Path) -> list[Word]:
        sidecar = audio_path.with_suffix(audio_path.suffix + ".json")
        if not sidecar.exists():
            sidecar = audio_path.with_suffix(".json")
        data = json.loads(sidecar.read_text())
        rows = data.get("words", data) if isinstance(data, dict) else data
        return [Word(str(row["text"]), float(row["start"]), float(row["end"]),
                     row.get("confidence")) for row in rows]


class FasterWhisperTranscriber:
    def __init__(self, model: str = "small"):
        self.model_name = model

    def transcribe(self, audio_path: Path) -> list[Word]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install wordbank[transcription] for faster-whisper") from exc
        model = WhisperModel(self.model_name)
        segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
        result = []
        for segment in segments:
            result.extend(Word(w.word, w.start, w.end, w.probability) for w in (segment.words or []))
        return result


def transcriber_from_env() -> Transcriber:
    kind = os.getenv("WORDBANK_TRANSCRIBER", "json").lower()
    if kind in {"whisper", "faster-whisper"}:
        return FasterWhisperTranscriber(os.getenv("WORDBANK_MODEL", "small"))
    if kind != "json":
        raise ValueError(f"Unknown WORDBANK_TRANSCRIBER: {kind}")
    return JSONTranscriber()
