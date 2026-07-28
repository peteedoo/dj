import json
import os
from pathlib import Path
from typing import Iterable, Protocol

from .models import Word


def clean_word_text(text: str) -> str:
    """Whisper family models emit leading spaces; strip for consistent tokens."""
    return str(text).strip()


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> Iterable[Word]: ...


class JSONTranscriber:
    def transcribe(self, audio_path: Path) -> list[Word]:
        sidecar = audio_path.with_suffix(audio_path.suffix + ".json")
        if not sidecar.exists():
            sidecar = audio_path.with_suffix(".json")
        data = json.loads(sidecar.read_text())
        rows = data.get("words", data) if isinstance(data, dict) else data
        return [
            Word(
                clean_word_text(row["text"]),
                float(row["start"]),
                float(row["end"]),
                row.get("confidence"),
            )
            for row in rows
        ]


class FasterWhisperTranscriber:
    def __init__(self, model: str = "small") -> None:
        self.model_name = model

    def transcribe(self, audio_path: Path) -> list[Word]:
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as exc:
            if exc.name == "faster_whisper":
                raise RuntimeError(
                    "Install wordbank[transcription] to use faster-whisper"
                ) from exc
            raise
        model = WhisperModel(self.model_name)
        segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
        result: list[Word] = []
        for segment in segments:
            for word in segment.words or []:
                result.append(
                    Word(
                        clean_word_text(word.word),
                        word.start,
                        word.end,
                        word.probability,
                    )
                )
        return result


class WhisperXTranscriber:
    """Whisper transcription plus wav2vec2 forced alignment (DJ-grade edges).

    Requires the optional ``alignment`` extra. Heavier than faster-whisper; use
    when ``small`` word boundaries are too soft on real acapellas.
    """

    def __init__(
        self,
        model: str = "small",
        device: str | None = None,
        compute_type: str | None = None,
        language: str = "en",
    ) -> None:
        self.model_name = model
        self.device = device or os.getenv("WORDBANK_DEVICE", "cpu")
        self.compute_type = compute_type or os.getenv(
            "WORDBANK_COMPUTE_TYPE",
            "int8" if self.device == "cpu" else "float16",
        )
        self.language = language

    def transcribe(self, audio_path: Path) -> list[Word]:
        try:
            import whisperx
        except ModuleNotFoundError as exc:
            if exc.name == "whisperx":
                raise RuntimeError(
                    "Install wordbank[alignment] to use WhisperX forced alignment"
                ) from exc
            raise

        audio = whisperx.load_audio(str(audio_path))
        model = whisperx.load_model(
            self.model_name,
            self.device,
            compute_type=self.compute_type,
            language=self.language,
        )
        transcribed = model.transcribe(audio, batch_size=8)
        language = transcribed.get("language") or self.language
        align_model, metadata = whisperx.load_align_model(
            language_code=language,
            device=self.device,
        )
        aligned = whisperx.align(
            transcribed["segments"],
            align_model,
            metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )
        result: list[Word] = []
        for segment in aligned.get("segments", []):
            for word in segment.get("words", []) or []:
                text = word.get("word") or word.get("text")
                if text is None:
                    continue
                start = word.get("start")
                end = word.get("end")
                if start is None or end is None:
                    continue
                score = word.get("score", word.get("confidence"))
                result.append(
                    Word(
                        clean_word_text(text),
                        float(start),
                        float(end),
                        float(score) if score is not None else None,
                    )
                )
        return result


def transcriber_from_env() -> Transcriber:
    kind = os.getenv("WORDBANK_TRANSCRIBER", "json").lower()
    model = os.getenv("WORDBANK_MODEL", "small")
    if kind in {"whisper", "faster-whisper"}:
        return FasterWhisperTranscriber(model)
    if kind in {"whisperx", "aligned", "alignment"}:
        return WhisperXTranscriber(model)
    if kind != "json":
        raise ValueError(f"Unknown WORDBANK_TRANSCRIBER: {kind}")
    return JSONTranscriber()
