import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from .audio import AudioToolError, duration, export_slice
from .export_util import sanitize_label_filename, unique_path
from .storage import Store
from .transcription import Transcriber, transcriber_from_env
from .youtube import (
    DEFAULT_CLIP_SECONDS,
    fetch_youtube_audio,
    looks_like_youtube,
    resolve_window,
)

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

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


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
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]


def default_data_dir() -> Path:
    """Ryan's sample library + SQLite home: ~/peteedoo/samples."""
    return Path.home() / "peteedoo" / "samples"


def resolve_dir(path: str | Path | None, fallback: Path) -> Path:
    if path is None or path == "":
        return fallback
    return Path(path).expanduser().resolve()


def compute_cut(
    word_start: float,
    word_end: float,
    clip_duration: float,
    pad_before: float,
    pad_after: float,
) -> tuple[float, float]:
    actual_start = max(0.0, word_start + pad_before)
    actual_end = min(clip_duration, word_end + pad_after)
    if actual_end <= actual_start:
        raise ValueError(
            f"Pads produce an empty cut "
            f"(start={actual_start:.3f}, end={actual_end:.3f})"
        )
    return actual_start, actual_end


class WordBank:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        transcriber: Transcriber | None = None,
        export_dir: str | Path | None = None,
    ) -> None:
        configured_dir = data_dir if data_dir is not None else os.getenv(
            "WORDBANK_DATA_DIR"
        )
        self.store = Store(resolve_dir(configured_dir, default_data_dir()))
        self.transcriber = transcriber or transcriber_from_env()
        configured_export = (
            export_dir if export_dir is not None else os.getenv("WORDBANK_EXPORT_DIR")
        )
        # Published label-named WAVs live in published/ by default so a DJ
        # watch folder is not pointed at sqlite / temps / hash library cuts.
        self.export_dir = resolve_dir(
            configured_export,
            self.store.data_dir / "published",
        )

    def _resolve_export_root(
        self,
        export_dir: str | Path | None,
        *,
        restrict: bool = True,
    ) -> Path:
        if export_dir is None or export_dir == "":
            root = self.export_dir
        else:
            root = Path(export_dir).expanduser().resolve()
            if restrict:
                try:
                    root.relative_to(self.export_dir.resolve())
                except ValueError as exc:
                    raise ValueError(
                        f"export_dir must be inside {self.export_dir}"
                    ) from exc
        root.mkdir(parents=True, exist_ok=True)
        return root

    def publish_sample(
        self,
        sample_id: int,
        export_dir: str | Path | None = None,
        *,
        restrict: bool = True,
    ) -> Path:
        """Copy a sample WAV into the DJ watch folder, named by label."""
        sample = self.store.sample(sample_id)
        if sample is None:
            raise ValueError("Sample not found")
        destination_root = self._resolve_export_root(export_dir, restrict=restrict)
        existing = sample.get("published_path")
        if existing and Path(existing).exists() and export_dir in (None, ""):
            target = Path(existing)
            shutil.copy2(sample["exported_path"], target)
        else:
            basename = sanitize_label_filename(
                sample["label"] or sample["text"] or f"sample-{sample_id}"
            )
            target = unique_path(destination_root, basename)
            shutil.copy2(sample["exported_path"], target)
        self.store.update_sample(sample_id, published_path=str(target))
        return target
    def ingest(
        self,
        source: Path,
        original_filename: str | None = None,
        speaker: str | None = None,
    ) -> int:
        suffix = source.suffix.lower()
        if suffix and suffix not in AUDIO_EXTENSIONS:
            raise ValueError(f"Unsupported audio extension: {suffix}")
        destination = self.store.audio_dir / (
            f"{source.stem}-{os.urandom(5).hex()}{suffix or '.wav'}"
        )
        try:
            shutil.copy2(source, destination)
            sidecars = (
                source.with_suffix(".json"),
                source.with_suffix(source.suffix + ".json"),
                Path(str(source) + ".json"),
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
        except Exception:
            destination.unlink(missing_ok=True)
            destination.with_suffix(".json").unlink(missing_ok=True)
            raise

    def ingest_youtube(
        self,
        url: str,
        speaker: str | None = None,
        start: str | float | int | None = None,
        end: str | float | int | None = None,
        duration_seconds: str | float | int | None = None,
        fetcher: Callable[..., dict[str, str | float]] = fetch_youtube_audio,
    ) -> int:
        """Download a YouTube time window (default 30s from start) and ingest it."""
        if not looks_like_youtube(url):
            raise ValueError("URL does not look like a YouTube link")
        start_s, end_s = resolve_window(
            start,
            end,
            duration_seconds,
            default_duration=DEFAULT_CLIP_SECONDS,
        )
        temp = self.store.tmp_dir / f"youtube-{os.urandom(5).hex()}.wav"
        try:
            meta = fetcher(url, temp, start_s, end_s)
            return self.ingest(
                temp,
                original_filename=str(meta["filename"]),
                speaker=speaker,
            )
        finally:
            temp.unlink(missing_ok=True)
            temp.with_suffix(".json").unlink(missing_ok=True)

    def ingest_batch(
        self,
        folder: Path,
        speaker: str | None = None,
    ) -> dict[str, Any]:
        """Ingest every audio file in a folder under one speaker label."""
        clips: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for path in discover_audio_files(folder):
            try:
                clip_id = self.ingest(path, speaker=speaker)
                clip = self.store.clip(clip_id)
                if clip is None:
                    raise RuntimeError(f"Ingested clip missing for {path}")
                clips.append(clip)
            except Exception as exc:  # noqa: BLE001 - collect per-file errors
                errors.append({"filename": path.name, "error": str(exc)})
        return {"clips": clips, "errors": errors, "count": len(clips)}

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
        *,
        restrict_publish: bool = True,
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
        actual_start, actual_end = compute_cut(
            words[0]["start"],
            words[-1]["end"],
            clip["duration"],
            pad_before,
            pad_after,
        )
        target = self.store.sample_dir / f"sample-{os.urandom(6).hex()}.wav"
        try:
            export_slice(
                Path(clip["audio_path"]),
                target,
                actual_start,
                actual_end,
            )
        except (AudioToolError, ValueError):
            target.unlink(missing_ok=True)
            raise
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
            try:
                published = self.publish_sample(
                    sample_id,
                    export_dir,
                    restrict=restrict_publish,
                )
                sample = dict(sample)
                sample["published_path"] = str(published)
            except Exception as exc:  # noqa: BLE001 - sample exists; report publish
                sample = dict(sample)
                sample["publish_error"] = str(exc)
        return sample

    def expand_sample(
        self,
        sample_id: int,
        before: int = 0,
        after: int = 0,
    ) -> dict[str, Any]:
        if before < 0 or after < 0:
            raise ValueError("before/after must be >= 0")
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
        if added_before == 0 and added_after == 0:
            return sample
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
        actual_start, actual_end = compute_cut(
            words[0]["start"],
            words[-1]["end"],
            clip["duration"],
            before,
            after,
        )
        target = Path(sample["exported_path"])
        export_slice(
            Path(clip["audio_path"]),
            target,
            actual_start,
            actual_end,
        )
        published = sample.get("published_path")
        if published:
            Path(published).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, published)
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

    def timing_report(self, clip_id: int) -> dict[str, Any]:
        """Summarize word-edge confidence to judge if alignment is needed."""
        clip = self.store.clip(clip_id)
        if clip is None:
            raise ValueError("Clip not found")
        words = clip["words"]
        scored = [word for word in words if word.get("confidence") is not None]
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
        low = sorted(scored, key=lambda word: word["confidence"])[:10]
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
