from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .service import WordBank


def create_app(bank=None):
    bank = bank or WordBank()
    app = FastAPI(title="Wordbank")

    @app.get("/health")
    def health(): return {"status": "ok"}

    @app.post("/clips")
    async def upload_clip(file: UploadFile = File(...), speaker: str | None = Form(None),
                          transcript: str | None = Form(None)):
        safe_name = Path(file.filename or "clip").name
        temp = bank.store.data_dir / f".upload-{safe_name}"
        temp.write_bytes(await file.read())
        if transcript:
            temp.with_suffix(".json").write_text(transcript)
        try:
            clip_id = bank.ingest(temp, file.filename, speaker)
            return bank.store.clip(clip_id)
        finally:
            temp.unlink(missing_ok=True)
            temp.with_suffix(".json").unlink(missing_ok=True)

    @app.get("/clips")
    def list_clips(): return bank.store.clips()

    @app.get("/clips/{clip_id}")
    def get_clip(clip_id: int):
        clip = bank.store.clip(clip_id)
        if not clip: raise HTTPException(404, "Clip not found")
        return clip

    @app.get("/clips/{clip_id}/audio")
    def clip_audio(clip_id: int):
        clip = bank.store.clip(clip_id)
        if not clip: raise HTTPException(404, "Clip not found")
        return FileResponse(clip["audio_path"])

    @app.get("/search")
    def search(q: str, speaker: str | None = None): return {"query": q, "hits": bank.store.search(q, speaker)}

    @app.post("/samples")
    def make_sample(payload: dict):
        try:
            return bank.make_sample(payload["clip_id"], payload["start_word"], payload["end_word"],
                                    payload.get("label", ""), float(payload.get("pad_before", -0.08)),
                                    float(payload.get("pad_after", 0.12)), payload.get("tags", ""))
        except (KeyError, ValueError) as exc: raise HTTPException(400, str(exc))

    @app.post("/samples/{sample_id}/expand")
    def expand_sample(sample_id: int, payload: dict):
        try: return bank.expand_sample(sample_id, int(payload.get("before", 0)), int(payload.get("after", 0)))
        except ValueError as exc: raise HTTPException(404, str(exc))

    @app.get("/samples")
    def list_samples(q: str | None = None, speaker: str | None = None, tag: str | None = None):
        return bank.store.samples(q, speaker, tag)

    @app.get("/samples/{sample_id}/audio")
    def sample_audio(sample_id: int):
        sample = bank.store.sample(sample_id)
        if not sample: raise HTTPException(404, "Sample not found")
        return FileResponse(sample["exported_path"])

    @app.delete("/samples/{sample_id}")
    def delete_sample(sample_id: int):
        if not bank.store.delete_sample(sample_id): raise HTTPException(404, "Sample not found")
        return {"deleted": True}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "index.html").read_text()
    return app
