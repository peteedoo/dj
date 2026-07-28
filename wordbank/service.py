import os
import shutil
from pathlib import Path

from .audio import duration, export_slice
from .storage import Store
from .transcription import Transcriber, transcriber_from_env


class WordBank:
    def __init__(self, data_dir=None, transcriber: Transcriber | None = None):
        self.store = Store(data_dir or os.getenv("WORDBANK_DATA_DIR", "wordbank/data"))
        self.transcriber = transcriber or transcriber_from_env()

    def ingest(self, source: Path, original_filename=None, speaker=None):
        destination = self.store.audio_dir / f"{source.stem}-{os.urandom(5).hex()}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        for sidecar in (source.with_suffix(".json"), source.with_suffix(source.suffix + ".json")):
            if sidecar.exists():
                shutil.copy2(sidecar, destination.with_suffix(".json"))
                break
        words = list(self.transcriber.transcribe(destination))
        return self.store.add_clip(destination, original_filename or source.name, speaker, duration(destination), words)

    def make_sample(self, clip_id, start_word, end_word, label="", pad_before=-0.08, pad_after=0.12, tags=""):
        clip = self.store.clip(clip_id)
        if not clip or not clip["words"]:
            raise ValueError("Clip or words not found")
        if start_word < 0 or end_word < start_word or end_word >= len(clip["words"]):
            raise ValueError("Invalid word span")
        words = clip["words"][start_word:end_word + 1]
        actual_start = max(0.0, words[0]["start"] + pad_before)
        actual_end = min(clip["duration"], words[-1]["end"] + pad_after)
        target = self.store.sample_dir / f"sample-{os.urandom(6).hex()}.wav"
        export_slice(Path(clip["audio_path"]), target, actual_start, actual_end)
        sample_id = self.store.add_sample(clip_id=clip_id, start_word_index=start_word, end_word_index=end_word,
                                          label=label or " ".join(w["raw_word"] for w in words),
                                          text=" ".join(w["raw_word"] for w in words), start_seconds=actual_start,
                                          end_seconds=actual_end, pad_before=pad_before, pad_after=pad_after,
                                          exported_path=str(target), speaker=clip["speaker"], tags=tags)
        return self.store.sample(sample_id)

    def expand_sample(self, sample_id, before=0, after=0):
        sample = self.store.sample(sample_id)
        if not sample:
            raise ValueError("Sample not found")
        return self.make_sample(sample["clip_id"], max(0, sample["start_word_index"] - before),
                                min(len(self.store.clip(sample["clip_id"])["words"]) - 1,
                                    sample["end_word_index"] + after), sample["label"],
                                sample["pad_before"], sample["pad_after"], sample["tags"])
