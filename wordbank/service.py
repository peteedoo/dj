import os
import shutil
from pathlib import Path
from typing import Any

from .audio import duration, export_slice
from .storage import Store
from .transcription import Transcriber, transcriber_from_env


class WordBank:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        configured_dir = data_dir or os.getenv(
            "WORDBANK_DATA_DIR", "wordbank/data"
        )
        self.store = Store(configured_dir)
        self.transcriber = transcriber or transcriber_from_env()

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

    def make_sample(
        self,
        clip_id: int,
        start_word: int,
        end_word: int,
        label: str = "",
        pad_before: float = -0.08,
        pad_after: float = 0.12,
        tags: str = "",
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
        expanded_label = (
            f"{sample['label']} "
            f"(expanded +{added_before} before, +{added_after} after)"
        )
        return self.make_sample(
            sample["clip_id"],
            start_word,
            end_word,
            expanded_label,
            sample["pad_before"],
            sample["pad_after"],
            sample["tags"],
        )
