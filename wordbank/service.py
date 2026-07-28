import os
import re
import shutil
from pathlib import Path
from typing import Any

from .audio import duration, export_slice
from .export_util import sanitize_label_filename, unique_path
from .storage import Store
from .transcription import Transcriber, transcriber_from_env

EXPANSION_SUFFIX = re.compile(r"^(?P<label>.*) \(expanded(?P<terms>.*)\)$")
EXPANSION_TERM = re.compile(r"([+-]\d+) (before|after)")

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".aiff",
    ".aif",
    ".m4a",
    ".ogg",
    ".aac",
}


def expanded_label(
    label: str,
    added_before: int,
    added_after: int,
) -> str:
    match = EXPANSION_SUFFIX.match(label)
    if match is None:
        base_label = label
        previous_before = 0
        previous_after = 0
    else:
        base_label = match.group("label")
        previous_before = 0
        previous_after = 0
        for value, direction in EXPANSION_TERM.findall(match.group("terms")):
            if direction == "before":
                previous_before = int(value)
            else:
                previous_after = int(value)

    total_before = previous_before + added_before
    total_after = previous_after + added_after
    terms = []
    if total_before:
        terms.append(f"+{total_before} before")
    if total_after:
        terms.append(f"+{total_after} after")
    if not terms:
        return base_label
    return f"{base_label} (expanded {', '.join(terms)})"


def discover_audio_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder}")
    files = [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return files


class WordBank:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        transcriber: Transcriber | None = None,
        export_dir: str | Path | None = None,
    ) -> None:
        configured_dir = data_dir or os.getenv(
            "WORDBANK_DATA_DIR", "wordbank/data"
        )
        self.store = Store(configured_dir)
        self.transcriber = transcriber or transcriber_from_env()
        configured_export = export_dir or os.getenv("WORDBANK_EXPORT_DIR")
        self.export_dir = (
            Path(configured_export)
            if configured_export
            else self.store.data_dir / "dj-export"
        )

    def ingest(
        self,
        source: Path,
        original_filename: str | None = None,
        speaker: str | None = None,
    ) -> int:
        destination = self.store.audio_dir / (
            f"{source.stem}-{os.urandom(5).hex()}{source.suffix.lower()}"
        )
        shutil.copy2(source, destination)
        sidecars = (
            source.with_suffix(".json"),
            source.with_suffix(source.suffix + ".json"),
        )
        for sidecar in sidecars:
            if sidecar.exists():
                shutil.copy2(sidecar, destination.with_suffix(".json"))
                break
        words = list(self.transcriber.transcribe(destination))
        return self.store.add_clip(
            destination,
            original_filename or source.name,
            speaker,
            duration(destination),
            words,
        )

    def ingest_batch(
        self,
        folder: Path,
        speaker: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ingest every audio file in a folder under one speaker label."""
        results: list[dict[str, Any]] = []
        for path in discover_audio_files(folder):
            clip_id = self.ingest(path, speaker=speaker)
            clip = self.store.clip(clip_id)
            if clip is None:
                raise RuntimeError(f"Ingested clip missing for {path}")
            results.append(clip)
        return results

    def make_sample(
        self,
        clip_id: int,
        start_word: int,
        end_word: int,
        label: str = "",
        pad_before: float = -0.08,
        pad_after: float = 0.12,
        tags: str = "",
        publish: bool = False,
        export_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        clip = self.store.clip(clip_id)
        if clip is None or not clip["words"]:
            raise ValueError("Clip or words not found")
        if (
            start_word < 0
            or end_word < start_word
            or end_word >= len(clip["words"])
        ):
            raise ValueError("Invalid word span")
        words = clip["words"][start_word : end_word + 1]
        actual_start = max(0.0, words[0]["start"] + pad_before)
        actual_end = min(clip["duration"], words[-1]["end"] + pad_after)
        target = self.store.sample_dir / f"sample-{os.urandom(6).hex()}.wav"
        export_slice(
            Path(clip["audio_path"]),
            target,
            actual_start,
            actual_end,
        )
        sample_id = self.store.add_sample(
            clip_id=clip_id,
            start_word_index=start_word,
            end_word_index=end_word,
            label=label or " ".join(word["raw_word"] for word in words),
            text=" ".join(word["raw_word"] for word in words),
            start_seconds=actual_start,
            end_seconds=actual_end,
            pad_before=pad_before,
            pad_after=pad_after,
            exported_path=str(target),
            speaker=clip["speaker"],
            tags=tags,
        )
        sample = self.store.sample(sample_id)
        if sample is None:
            raise RuntimeError("Created sample could not be loaded")
        if publish:
            published = self.publish_sample(sample_id, export_dir)
            sample = dict(sample)
            sample["published_path"] = str(published)
        return sample

    def expand_sample(
        self,
        sample_id: int,
        before: int = 0,
        after: int = 0,
    ) -> dict[str, Any]:
        sample = self.store.sample(sample_id)
        if sample is None:
            raise ValueError("Sample not found")
        clip = self.store.clip(sample["clip_id"])
        if clip is None:
            raise ValueError("Clip not found")
        start_word = max(0, sample["start_word_index"] - before)
        end_word = min(
            len(clip["words"]) - 1,
            sample["end_word_index"] + after,
        )
        added_before = sample["start_word_index"] - start_word
        added_after = end_word - sample["end_word_index"]
        label = expanded_label(sample["label"], added_before, added_after)
        return self.make_sample(
            sample["clip_id"],
            start_word,
            end_word,
            label,
            sample["pad_before"],
            sample["pad_after"],
            sample["tags"],
        )

    def recut_sample(
        self,
        sample_id: int,
        pad_before: float | None = None,
        pad_after: float | None = None,
    ) -> dict[str, Any]:
        """Re-cut the same word indices with new pad values (in place)."""
        sample = self.store.sample(sample_id)
        if sample is None:
            raise ValueError("Sample not found")
        clip = self.store.clip(sample["clip_id"])
        if clip is None or not clip["words"]:
            raise ValueError("Clip or words not found")
        before = sample["pad_before"] if pad_before is None else pad_before
        after = sample["pad_after"] if pad_after is None else pad_after
        words = clip["words"][
            sample["start_word_index"] : sample["end_word_index"] + 1
        ]
        actual_start = max(0.0, words[0]["start"] + before)
        actual_end = min(clip["duration"], words[-1]["end"] + after)
        target = Path(sample["exported_path"])
        export_slice(
            Path(clip["audio_path"]),
            target,
            actual_start,
            actual_end,
        )
        updated = self.store.update_sample(
            sample_id,
            start_seconds=actual_start,
            end_seconds=actual_end,
            pad_before=before,
            pad_after=after,
        )
        if updated is None:
            raise RuntimeError("Updated sample could not be loaded")
        return updated

    def publish_sample(
        self,
        sample_id: int,
        export_dir: str | Path | None = None,
    ) -> Path:
        """Copy a sample WAV into a Serato/Rekordbox-watched folder by label."""
        sample = self.store.sample(sample_id)
        if sample is None:
            raise ValueError("Sample not found")
        destination_root = Path(export_dir) if export_dir else self.export_dir
        destination_root.mkdir(parents=True, exist_ok=True)
        basename = sanitize_label_filename(
            sample["label"] or sample["text"] or f"sample-{sample_id}"
        )
        target = unique_path(destination_root, basename)
        shutil.copy2(sample["exported_path"], target)
        return target

    def timing_report(self, clip_id: int) -> dict[str, Any]:
        """Summarize word-edge confidence to judge if alignment is needed."""
        clip = self.store.clip(clip_id)
        if clip is None:
            raise ValueError("Clip not found")
        words = clip["words"]
        scored = [
            word
            for word in words
            if word.get("confidence") is not None
        ]
        gaps = []
        for previous, current in zip(words, words[1:]):
            gap = current["start"] - previous["end"]
            if gap < -0.02 or gap > 0.25:
                gaps.append(
                    {
                        "after_position": previous["position"],
                        "gap": gap,
                        "left": previous["raw_word"],
                        "right": current["raw_word"],
                    }
                )
        low = sorted(
            scored,
            key=lambda word: word["confidence"],
        )[:10]
        average = (
            sum(word["confidence"] for word in scored) / len(scored)
            if scored
            else None
        )
        return {
            "clip_id": clip_id,
            "filename": clip["original_filename"],
            "speaker": clip["speaker"],
            "word_count": len(words),
            "scored_words": len(scored),
            "average_confidence": average,
            "low_confidence": [
                {
                    "position": word["position"],
                    "word": word["raw_word"],
                    "confidence": word["confidence"],
                    "start": word["start"],
                    "end": word["end"],
                }
                for word in low
            ],
            "suspicious_gaps": gaps,
            "hint": (
                "If edges feel soft on real acapellas, set "
                "WORDBANK_TRANSCRIBER=whisperx (wordbank[alignment]) and re-ingest."
            ),
        }
