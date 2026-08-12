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
        if not sidecar.exists():
            raise FileNotFoundError(
                f"JSON transcriber needs a sidecar next to {audio_path.name}"
            )
        data = json.loads(sidecar.read_text())
        if isinstance(data, dict):
            if "words" not in data or not isinstance(data["words"], list):
                raise ValueError("Transcript JSON must contain a 'words' list")
            rows = data["words"]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError("Transcript JSON must be a list or {words:[...]}")
        words: list[Word] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Word entry {index} must be an object")
            try:
                text = clean_word_text(row["text"])
                start = float(row["start"])
                end = float(row["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid word entry {index}: {exc}") from exc
            if end <= start:
                raise ValueError(
                    f"Word entry {index} has end <= start ({start}, {end})"
                )
            confidence = row.get("confidence")
            if confidence is not None:
                confidence = float(confidence)
            words.append(Word(text, start, end, confidence))
        return words


class FasterWhisperTranscriber:
    def __init__(self, model: str = "small") -> None:
        self.model_name = model
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as exc:
            if exc.name == "faster_whisper":
                raise RuntimeError(
                    "Install wordbank[transcription] to use faster-whisper"
                ) from exc
            raise
        self._model = WhisperModel(self.model_name)
        return self._model

    def transcribe(self, audio_path: Path) -> list[Word]:
        model = self._load()
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
    """Whisper transcription plus wav2vec2 forced alignment (DJ-grade edges)."""

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
        self._model = None
        self._align_model = None
        self._align_metadata = None
        self._align_language = None

    def _load_asr(self):
        if self._model is not None:
            return self._model
        try:
            import whisperx
        except ModuleNotFoundError as exc:
            if exc.name == "whisperx":
                raise RuntimeError(
                    "Install wordbank[alignment] to use WhisperX forced alignment"
                ) from exc
            raise
        self._whisperx = whisperx
        self._model = whisperx.load_model(
            self.model_name,
            self.device,
            compute_type=self.compute_type,
            language=self.language,
        )
        return self._model

    def _load_align(self, language: str):
        if self._align_model is not None and self._align_language == language:
            return self._align_model, self._align_metadata
        whisperx = self._whisperx
        self._align_model, self._align_metadata = whisperx.load_align_model(
            language_code=language,
            device=self.device,
        )
        self._align_language = language
        return self._align_model, self._align_metadata

    def transcribe(self, audio_path: Path) -> list[Word]:
        model = self._load_asr()
        whisperx = self._whisperx
        audio = whisperx.load_audio(str(audio_path))
        transcribed = model.transcribe(audio, batch_size=8)
        language = transcribed.get("language") or self.language
        align_model, metadata = self._load_align(language)
        aligned = whisperx.align(
            transcribed["segments"],
            align_model,
            metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )
        result: list[Word] = []
        dropped = 0
        for segment in aligned.get("segments", []):
            for word in segment.get("words", []) or []:
                text = word.get("word") or word.get("text")
                if text is None:
                    continue
                start = word.get("start")
                end = word.get("end")
                if start is None or end is None:
                    dropped += 1
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
        if dropped and dropped >= max(3, len(result) // 5):
            raise RuntimeError(
                f"WhisperX dropped {dropped} words without timings "
                f"({len(result)} kept); try re-ingest or another model"
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
