import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .service import AUDIO_EXTENSIONS, MAX_UPLOAD_BYTES, WordBank


def _audio_media_type(path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
    }.get(path.suffix.lower(), "application/octet-stream")


def _unique_tmp(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{os.urandom(6).hex()}-{Path(name).name}"


async def _stream_upload(upload: UploadFile, destination: Path) -> None:
    total = 0
    with destination.open("wb") as handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    413,
                    f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
                )
            handle.write(chunk)


def create_app(bank: WordBank | None = None) -> FastAPI:
    wordbank = bank or WordBank()
    app = FastAPI(title="Wordbank")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/clips")
    async def upload_clip(
        file: UploadFile = File(...),
        speaker: str | None = Form(None),
        transcript: str | None = Form(None),
    ) -> dict[str, Any] | None:
        safe_name = Path(file.filename or "clip.wav").name
        suffix = Path(safe_name).suffix.lower()
        if suffix and suffix not in AUDIO_EXTENSIONS:
            raise HTTPException(400, f"Unsupported audio extension: {suffix}")
        temp = _unique_tmp(wordbank.store.tmp_dir, safe_name)
        try:
            await _stream_upload(file, temp)
            if transcript:
                Path(str(temp) + ".json").write_text(transcript)
            try:
                clip_id = wordbank.ingest(temp, file.filename, speaker)
            except Exception as exc:  # noqa: BLE001 - surface ingest errors
                raise HTTPException(400, str(exc)) from exc
            return wordbank.store.clip(clip_id)
        finally:
            temp.unlink(missing_ok=True)
            Path(str(temp) + ".json").unlink(missing_ok=True)
            temp.with_suffix(".json").unlink(missing_ok=True)

    @app.post("/clips/batch")
    async def upload_batch(
        files: list[UploadFile] = File(...),
        speaker: str | None = Form(None),
    ) -> dict[str, Any]:
        clips: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, upload in enumerate(files):
            safe_name = Path(upload.filename or f"clip-{index}.wav").name
            temp = _unique_tmp(wordbank.store.tmp_dir, safe_name)
            try:
                suffix = Path(safe_name).suffix.lower()
                if suffix and suffix not in AUDIO_EXTENSIONS:
                    raise ValueError(f"Unsupported audio extension: {suffix}")
                await _stream_upload(upload, temp)
                clip_id = wordbank.ingest(temp, upload.filename, speaker)
                clip = wordbank.store.clip(clip_id)
                if clip is not None:
                    clips.append(clip)
            except Exception as exc:  # noqa: BLE001 - report per-file failure
                errors.append({"filename": safe_name, "error": str(exc)})
            finally:
                temp.unlink(missing_ok=True)
                temp.with_suffix(".json").unlink(missing_ok=True)
        payload = {"clips": clips, "errors": errors, "count": len(clips), "ok": bool(clips)}
        if not clips and errors:
            raise HTTPException(400, payload)
        return payload

    @app.post("/clips/youtube")
    async def ingest_youtube(payload: dict[str, Any]) -> dict[str, Any]:
        url = (payload.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url is required")
        try:
            clip_id = await asyncio.to_thread(
                wordbank.ingest_youtube,
                url,
                payload.get("speaker"),
                payload.get("start"),
                payload.get("end"),
                payload.get("duration"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        clip = wordbank.store.clip(clip_id)
        if clip is None:
            raise HTTPException(500, "Ingested clip missing")
        return clip

    @app.get("/clips")
    def list_clips() -> list[dict[str, Any]]:
        return wordbank.store.clips()

    @app.get("/clips/{clip_id}")
    def get_clip(clip_id: int) -> dict[str, Any]:
        clip = wordbank.store.clip(clip_id)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        return clip

    @app.delete("/clips/{clip_id}")
    def delete_clip(clip_id: int) -> dict[str, bool]:
        if not wordbank.store.delete_clip(clip_id):
            raise HTTPException(404, "Clip not found")
        return {"deleted": True}

    @app.get("/clips/{clip_id}/audio")
    def clip_audio(clip_id: int) -> FileResponse:
        clip = wordbank.store.clip(clip_id)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        path = Path(clip["audio_path"])
        if not path.is_file():
            raise HTTPException(404, "Audio file missing on disk")
        return FileResponse(
            path,
            media_type=_audio_media_type(path),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/clips/{clip_id}/timing")
    def clip_timing(clip_id: int) -> dict[str, Any]:
        try:
            return wordbank.timing_report(clip_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/search")
    def search(
        q: str,
        speaker: str | None = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        return {
            "query": q,
            "hits": wordbank.store.search(q, speaker, limit),
        }

    @app.post("/samples")
    def make_sample(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return wordbank.make_sample(
                int(payload["clip_id"]),
                int(payload["start_word"]),
                int(payload["end_word"]),
                payload.get("label", ""),
                float(payload.get("pad_before", -0.08)),
                float(payload.get("pad_after", 0.12)),
                payload.get("tags", ""),
                bool(payload.get("publish", False)),
                payload.get("export_dir"),
            )
        except KeyError as exc:
            raise HTTPException(400, f"Missing field: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/samples/{sample_id}/expand")
    def expand_sample(
        sample_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sample = wordbank.store.sample(sample_id)
        if sample is None:
            raise HTTPException(404, "Sample not found")
        try:
            before = int(payload.get("before") or 0)
            after = int(payload.get("after") or 0)
            return wordbank.expand_sample(sample_id, before, after)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/samples/{sample_id}/recut")
    def recut_sample(
        sample_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sample = wordbank.store.sample(sample_id)
        if sample is None:
            raise HTTPException(404, "Sample not found")
        try:
            pad_before = payload.get("pad_before")
            pad_after = payload.get("pad_after")
            return wordbank.recut_sample(
                sample_id,
                float(pad_before) if pad_before is not None else None,
                float(pad_after) if pad_after is not None else None,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/samples/{sample_id}/publish")
    def publish_sample(
        sample_id: int,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        sample = wordbank.store.sample(sample_id)
        if sample is None:
            raise HTTPException(404, "Sample not found")
        try:
            path = wordbank.publish_sample(sample_id, payload.get("export_dir"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"published_path": str(path)}

    @app.get("/samples")
    def list_samples(
        q: str | None = None,
        speaker: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        return wordbank.store.samples(q, speaker, tag)

    @app.get("/samples/{sample_id}/audio")
    def sample_audio(sample_id: int) -> FileResponse:
        sample = wordbank.store.sample(sample_id)
        if sample is None:
            raise HTTPException(404, "Sample not found")
        path = Path(sample["exported_path"])
        if not path.is_file():
            raise HTTPException(404, "Audio file missing on disk")
        return FileResponse(
            path,
            media_type=_audio_media_type(path),
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/samples/{sample_id}")
    def delete_sample(sample_id: int) -> dict[str, bool]:
        if not wordbank.store.delete_sample(sample_id):
            raise HTTPException(404, "Sample not found")
        return {"deleted": True}

    @app.get("/settings")
    def settings() -> dict[str, Any]:
        return {
            "export_dir": str(wordbank.export_dir),
            "data_dir": str(wordbank.store.data_dir),
            "audio_extensions": sorted(
                extension.lstrip(".") for extension in AUDIO_EXTENSIONS
            ),
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        path = Path(__file__).parent / "static" / "index.html"
        if not path.is_file():
            raise HTTPException(500, "UI assets missing from package")
        return path.read_text()

    return app
