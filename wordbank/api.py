from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .service import AUDIO_EXTENSIONS, WordBank


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
        safe_name = Path(file.filename or "clip").name
        temp = wordbank.store.data_dir / f".upload-{safe_name}"
        temp.write_bytes(await file.read())
        if transcript:
            temp.with_suffix(".json").write_text(transcript)
        try:
            clip_id = wordbank.ingest(temp, file.filename, speaker)
            return wordbank.store.clip(clip_id)
        finally:
            temp.unlink(missing_ok=True)
            temp.with_suffix(".json").unlink(missing_ok=True)

    @app.post("/clips/batch")
    async def upload_batch(
        files: list[UploadFile] = File(...),
        speaker: str | None = Form(None),
    ) -> dict[str, Any]:
        clips: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for upload in files:
            safe_name = Path(upload.filename or "clip").name
            temp = wordbank.store.data_dir / f".upload-batch-{safe_name}"
            try:
                temp.write_bytes(await upload.read())
                clip_id = wordbank.ingest(temp, upload.filename, speaker)
                clip = wordbank.store.clip(clip_id)
                if clip is not None:
                    clips.append(clip)
            except Exception as exc:  # noqa: BLE001 - report per-file failure
                errors.append({"filename": safe_name, "error": str(exc)})
            finally:
                temp.unlink(missing_ok=True)
                temp.with_suffix(".json").unlink(missing_ok=True)
        return {"clips": clips, "errors": errors, "count": len(clips)}

    @app.get("/clips")
    def list_clips() -> list[dict[str, Any]]:
        return wordbank.store.clips()

    @app.get("/clips/{clip_id}")
    def get_clip(clip_id: int) -> dict[str, Any]:
        clip = wordbank.store.clip(clip_id)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        return clip

    @app.get("/clips/{clip_id}/audio")
    def clip_audio(clip_id: int) -> FileResponse:
        clip = wordbank.store.clip(clip_id)
        if clip is None:
            raise HTTPException(404, "Clip not found")
        return FileResponse(clip["audio_path"])

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
        limit: int = 50,
    ) -> dict[str, Any]:
        return {
            "query": q,
            "hits": wordbank.store.search(q, speaker, limit),
        }

    @app.post("/samples")
    def make_sample(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return wordbank.make_sample(
                payload["clip_id"],
                payload["start_word"],
                payload["end_word"],
                payload.get("label", ""),
                float(payload.get("pad_before", -0.08)),
                float(payload.get("pad_after", 0.12)),
                payload.get("tags", ""),
                bool(payload.get("publish", False)),
                payload.get("export_dir"),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/samples/{sample_id}/expand")
    def expand_sample(
        sample_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return wordbank.expand_sample(
                sample_id,
                int(payload.get("before", 0)),
                int(payload.get("after", 0)),
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/samples/{sample_id}/recut")
    def recut_sample(
        sample_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            pad_before = payload.get("pad_before")
            pad_after = payload.get("pad_after")
            return wordbank.recut_sample(
                sample_id,
                float(pad_before) if pad_before is not None else None,
                float(pad_after) if pad_after is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/samples/{sample_id}/publish")
    def publish_sample(
        sample_id: int,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        try:
            path = wordbank.publish_sample(
                sample_id,
                payload.get("export_dir"),
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
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
        return FileResponse(sample["exported_path"])

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
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (Path(__file__).parent / "static" / "index.html").read_text()

    return app
