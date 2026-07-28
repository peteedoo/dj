import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wordbank.api import create_app
from wordbank.service import WordBank
from wordbank.transcription import JSONTranscriber


@pytest.fixture
def clip_file(tmp_path):
    audio = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-ac", "1", "-ar", "16000", str(audio)], capture_output=True, check=True)
    (tmp_path / "voice.json").write_text(json.dumps({"words": [
        {"text": "Hello,", "start": 0.05, "end": 0.35, "confidence": .9},
        {"text": "world", "start": 0.45, "end": 0.8, "confidence": .9},
        {"text": "hello", "start": 1.1, "end": 1.4, "confidence": .8},
    ]}))
    return audio


@pytest.fixture
def bank(tmp_path, clip_file):
    return WordBank(tmp_path / "data", JSONTranscriber())


def test_index_search_speaker_and_context(bank, clip_file):
    one = bank.ingest(clip_file, speaker="Ada")
    two = bank.ingest(clip_file, speaker="Bob")
    assert bank.store.clip(one)["transcript"] == "Hello, world hello"
    hits = bank.store.search("hello world", "Ada")
    assert len(hits) == 1 and hits[0]["context_after"] == ["hello"]
    assert bank.store.search("hello world", "Bob")[0]["clip_id"] == two
    assert bank.store.search("world hello")[0]["word_start"] == 1


def test_export_padding_clamp_duration_and_expand(bank, clip_file):
    clip_id = bank.ingest(clip_file, speaker="Ada")
    sample = bank.make_sample(clip_id, 0, 0, pad_before=-2, pad_after=2)
    assert sample["start_seconds"] == 0
    assert sample["end_seconds"] == pytest.approx(2, abs=.05)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", sample["exported_path"]],
                           capture_output=True, text=True, check=True)
    assert float(probe.stdout) == pytest.approx(2, abs=.08)
    expanded = bank.expand_sample(sample["id"], after=2)
    assert expanded["end_word_index"] == 2


def test_api_routes(tmp_path, clip_file):
    bank = WordBank(tmp_path / "data", JSONTranscriber())
    client = TestClient(create_app(bank))
    assert client.get("/health").json() == {"status": "ok"}
    with clip_file.open("rb") as fh:
        transcript = json.dumps({"words": [
            {"text": "Hello,", "start": .05, "end": .35},
            {"text": "world", "start": .45, "end": .8},
            {"text": "hello", "start": 1.1, "end": 1.4},
        ]})
        response = client.post("/clips", files={"file": ("clip.wav", fh, "audio/wav")},
                               data={"speaker": "Ada", "transcript": transcript})
    assert response.status_code == 200
    clip = response.json()
    assert len(client.get(f"/clips/{clip['id']}").json()["words"]) == 3
    assert client.get("/search?q=hello+world&speaker=Ada").json()["hits"][0]["start"] == .05
    sample = client.post("/samples", json={"clip_id": clip["id"], "start_word": 0, "end_word": 1}).json()
    assert client.get(f"/samples/{sample['id']}/audio").status_code == 200
    assert client.delete(f"/samples/{sample['id']}").json() == {"deleted": True}
