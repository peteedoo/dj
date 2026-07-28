# Wordbank

Wordbank indexes spoken clips at word-level timestamps so DJs can find phrases,
cue playback, and export padded WAV samples. It stores metadata in SQLite and
keeps source and exported audio below a configurable data directory.

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

For real transcription install `pip install -e '.[transcription]'` and set
`WORDBANK_TRANSCRIBER=faster-whisper` (optionally `WORDBANK_MODEL=small`).

## Environment

* `WORDBANK_DATA_DIR` — SQLite/audio directory, default `wordbank/data/`.
* `WORDBANK_TRANSCRIBER` — `json` (default) or `faster-whisper`.
* `WORDBANK_MODEL` — faster-whisper model name, default `small`.

## CLI

```bash
wordbank ingest clip.wav --speaker "MC"
wordbank search "make some noise" --speaker "MC"
wordbank export 1 2 4 --label "noise phrase"
wordbank serve --host 0.0.0.0 --port 8000
```

## API

* `GET /health`
* `POST /clips` multipart fields `file`, optional `speaker`, and optional
  `transcript` JSON (useful with the JSON transcriber)
* `GET /clips`, `GET /clips/{id}`, `GET /clips/{id}/audio`
* `GET /search?q=phrase&speaker=...`
* `POST /samples` JSON `{clip_id,start_word,end_word,label,pad_before,pad_after,tags}`
* `POST /samples/{id}/expand` JSON `{before,after}`
* `GET /samples?q=...&speaker=...&tag=...`
* `GET /samples/{id}/audio`, `DELETE /samples/{id}`

Samples default to 80ms before and 120ms after the selected word span,
clamped to clip bounds, with short fades to avoid clicks.
