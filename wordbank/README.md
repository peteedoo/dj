# Wordbank

Wordbank indexes spoken clips at word-level timestamps so DJs can find phrases,
cue playback, and export padded WAV samples. It stores metadata in SQLite and
keeps source and exported audio below a configurable data directory.

A sample is stored as **word indices into a clip** (plus pad values), not as an
opaque cut. Expanding context re-cuts from the source and creates a *new*
sample so the tighter original remains.

## Setup

Python 3.10+ and `ffmpeg`/`ffprobe` are required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
wordbank serve
```

Open http://127.0.0.1:8000. The default transcriber is the JSON sidecar
transcriber, useful for offline operation and tests. A clip `speech.wav` can
have `speech.json` containing `{"words":[{"text":"hello","start":0.1,"end":0.4}]}`.

For real transcription:

```bash
pip install -e '.[transcription]'
WORDBANK_TRANSCRIBER=faster-whisper WORDBANK_MODEL=small wordbank serve
```

Use `tiny.en` only for quick smoke tests. If word edges on real acapellas feel
soft, install forced alignment and switch:

```bash
pip install -e '.[alignment]'
WORDBANK_TRANSCRIBER=whisperx WORDBANK_MODEL=small wordbank serve
```

Then run `wordbank timing CLIP_ID` (or the TIMING REPORT button) to inspect
confidence and suspicious gaps.

## Environment

* `WORDBANK_DATA_DIR` — SQLite + working audio directory, default
  `~/peteedoo/samples` (`wordbank.sqlite3`, `audio/`, `samples/`).
* `WORDBANK_EXPORT_DIR` — folder for published label-named WAVs (Serato /
  Rekordbox watch). Defaults to the same path as `WORDBANK_DATA_DIR`
  (`~/peteedoo/samples`), so `publish` drops `Make_Some_Noise.wav` next to
  the database.
* `WORDBANK_TRANSCRIBER` — `json` (default), `faster-whisper` / `whisper`, or
  `whisperx` / `aligned`.
* `WORDBANK_MODEL` — model name, default `small`.
* `WORDBANK_DEVICE` / `WORDBANK_COMPUTE_TYPE` — WhisperX device settings.

## CLI

```bash
wordbank ingest clip.wav --speaker "MC"
wordbank ingest-batch ./acapellas/mc --speaker "MC"
wordbank search "make some noise" --speaker "MC"
wordbank export 1 2 4 --label "noise phrase" --publish
wordbank publish 3 --dest "/Volumes/Music_Studio/DJ Music/Wordbank"
wordbank timing 1
wordbank serve   # uses ~/peteedoo/samples for DB + published WAVs
wordbank --data-dir ~/peteedoo/samples --export-dir ~/peteedoo/samples serve
```

## UI notes

* **Batch ingest** — select multiple files, set a speaker label, click BATCH INGEST.
* **Keyboard auditioning** — after search: ↑/↓ or j/k through hits, Enter/Space
  to preview, ←/→ to nudge the word selection, Esc to clear.
* **Waveform** — purple handles are pad edges; drag them instead of typing
  Before/After numbers. Blue markers are the raw word span.
* **Publish** — copies the sample WAV into the export folder, named from the
  label (`Make_Some_Noise.wav`). Expand still creates a new sample and leaves
  the original intact.

Speaker is always a **label typed at upload**, not diarization.

## API

* `GET /health`, `GET /settings`
* `POST /clips` multipart fields `file`, optional `speaker`, optional
  `transcript` JSON (useful with the JSON transcriber)
* `POST /clips/batch` multipart `files` (repeated) + optional `speaker`
* `GET /clips`, `GET /clips/{id}`, `GET /clips/{id}/audio`,
  `GET /clips/{id}/timing`
* `GET /search?q=phrase&speaker=...&limit=50`
* `POST /samples` JSON
  `{clip_id,start_word,end_word,label,pad_before,pad_after,tags,publish,export_dir}`
* `POST /samples/{id}/expand` JSON `{before,after}`
* `POST /samples/{id}/recut` JSON `{pad_before,pad_after}` — same word indices,
  new pads, re-cut in place
* `POST /samples/{id}/publish` JSON `{export_dir?}`
* `GET /samples?q=...&speaker=...&tag=...`
* `GET /samples/{id}/audio`, `DELETE /samples/{id}`

Samples default to 80ms before and 120ms after the selected word span,
clamped to clip bounds, with short fades to avoid clicks.
