---
name: testing-wordbank
description: How to run the wordbank spoken-word indexer locally and test it end-to-end, including offline transcription for tests, real faster-whisper setup, and generating speech fixtures. Use when changing anything under wordbank/.
---

# Testing wordbank

`wordbank/` transcribes audio to word-level timestamps, indexes every word, and exports padded WAV
samples. It shells out to `ffmpeg`/`ffprobe`, so both must be on PATH.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test,transcription]'
```

## Running the tests

```bash
.venv/bin/pytest
```

Tests use `JSONTranscriber`, which reads a `.json` sidecar next to the audio file instead of calling
whisper. The suite therefore runs fully offline in about a second and never downloads a model.
**Keep it that way** — do not add a test that pulls whisper weights.

Sidecar format (`clip.wav` → `clip.json`, or `clip.wav.json`):

```json
{"words": [{"text": "Make", "start": 0.05, "end": 0.35}]}
```

## Running the server

```bash
# Offline JSON-sidecar mode
.venv/bin/wordbank serve

# Real transcription (downloads the model on first run)
WORDBANK_TRANSCRIBER=faster-whisper WORDBANK_MODEL=tiny.en \
  .venv/bin/wordbank --data-dir /tmp/wbdata serve --port 8000
```

Serves the UI and API on `127.0.0.1:8000`; `GET /health` returns `{"status":"ok"}`. Always use a
throwaway `--data-dir` when testing. Start it as a long-lived foreground process in its own shell —
backgrounding it inside a short-lived command shell kills it when that shell exits.

`tiny.en` is for fast testing only; real material should use the default `small`.

## Generating speech fixtures

```bash
espeak-ng -s 130 -v en-us "Drop the beat. Make some noise." -w raw.wav
ffmpeg -y -i raw.wav -ar 16000 -ac 1 speech.wav
```

Use a different `-v` voice for a second fixture when testing speaker filtering. Note that `tiny.en`
mishears robotic TTS fairly often — fixtures prove the plumbing, not transcription accuracy.

## Verifying exports objectively

Don't trust the audio player's rounded duration. Check the real thing:

```bash
curl -s -o /tmp/s.wav http://127.0.0.1:8000/samples/1/audio
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 /tmp/s.wav
```

Expected duration is `(last_word.end + pad_after) - (first_word.start + pad_before)`, clamped to the
clip, with defaults `pad_before=-0.08` and `pad_after=0.12`.

## Traps

- `faster-whisper` 1.1.1 imports `requests` without declaring it; it's pinned in the `transcription`
  extra for that reason. Don't remove it, and verify optional extras in a clean venv.
- Whisper returns tokens with leading spaces (`' Make'`). They're stripped on ingest — any new
  transcriber must produce clean tokens or the UI renders doubled spaces.
- Phrase search is position-aware and returns *every* occurrence, including repeats inside one clip.
  A transcript substring search would return one hit per clip and break the core use case.
- Speaker is a label typed at upload, not diarization.
