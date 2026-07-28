import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wordbank.api import create_app
from wordbank import cli
from wordbank.export_util import sanitize_label_filename, unique_path
from wordbank.service import (
    WordBank,
    default_data_dir,
    discover_audio_files,
    resolve_dir,
)
from wordbank.transcription import (
    FasterWhisperTranscriber,
    JSONTranscriber,
    WhisperXTranscriber,
    clean_word_text,
    transcriber_from_env,
)


@pytest.fixture
def clip_file(tmp_path):
    audio = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio),
        ],
        capture_output=True,
        check=True,
    )
    (tmp_path / "voice.json").write_text(
        json.dumps(
            {
                "words": [
                    {"text": "Hello,", "start": 0.05, "end": 0.35, "confidence": 0.9},
                    {"text": "world", "start": 0.45, "end": 0.8, "confidence": 0.9},
                    {"text": "hello", "start": 1.1, "end": 1.4, "confidence": 0.8},
                ]
            }
        )
    )
    return audio


@pytest.fixture
def whisper_clip_file(tmp_path):
    audio = tmp_path / "whisper.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio),
        ],
        capture_output=True,
        check=True,
    )
    (tmp_path / "whisper.json").write_text(
        json.dumps(
            {
                "words": [
                    {"text": " Make", "start": 0.05, "end": 0.35},
                    {"text": " some", "start": 0.45, "end": 0.8},
                    {"text": " noise.", "start": 1.1, "end": 1.4},
                ]
            }
        )
    )
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
    assert len(bank.store.search("hello", limit=1)) == 1


def test_transcriber_selection(monkeypatch):
    monkeypatch.setenv("WORDBANK_TRANSCRIBER", "json")
    assert isinstance(transcriber_from_env(), JSONTranscriber)
    monkeypatch.setenv("WORDBANK_TRANSCRIBER", "whisper")
    assert isinstance(transcriber_from_env(), FasterWhisperTranscriber)
    monkeypatch.setenv("WORDBANK_TRANSCRIBER", "whisperx")
    assert isinstance(transcriber_from_env(), WhisperXTranscriber)
    monkeypatch.setenv("WORDBANK_TRANSCRIBER", "other")
    with pytest.raises(ValueError, match="Unknown"):
        transcriber_from_env()


def test_clean_word_text_strips_whisper_spaces():
    assert clean_word_text(" Make") == "Make"
    assert clean_word_text(" noise. ") == "noise."


def test_whisperx_missing_dependency_message(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "whisperx" or name.startswith("whisperx."):
            raise ModuleNotFoundError("No module named 'whisperx'", name="whisperx")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="wordbank\\[alignment\\]"):
        WhisperXTranscriber().transcribe(Path("missing.wav"))


def test_export_padding_clamp_duration_and_expand(bank, clip_file):
    clip_id = bank.ingest(clip_file, speaker="Ada")
    sample = bank.make_sample(clip_id, 0, 0, pad_before=-2, pad_after=2)
    assert sample["start_seconds"] == 0
    assert sample["end_seconds"] == pytest.approx(2, abs=0.05)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            sample["exported_path"],
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(probe.stdout) == pytest.approx(2, abs=0.08)
    expanded = bank.expand_sample(sample["id"], after=2)
    assert expanded["end_word_index"] == 2
    assert expanded["label"] == "Hello, (expanded +2 after)"

    expanded_again = bank.expand_sample(expanded["id"], after=1)
    assert expanded_again["label"] == "Hello, (expanded +2 after)"


def test_expansion_label_accumulates_current_span_once(bank, clip_file):
    clip_id = bank.ingest(clip_file, speaker="Ada")
    sample = bank.make_sample(clip_id, 0, 0, label="hello")

    first_expansion = bank.expand_sample(sample["id"], after=1)
    second_expansion = bank.expand_sample(first_expansion["id"], after=1)

    assert first_expansion["label"] == "hello (expanded +1 after)"
    assert second_expansion["label"] == "hello (expanded +2 after)"
    assert second_expansion["label"].count("(expanded") == 1


def test_ingest_strips_transcription_token_whitespace(tmp_path, whisper_clip_file):
    bank = WordBank(tmp_path / "data", JSONTranscriber())

    clip_id = bank.ingest(whisper_clip_file)
    clip = bank.store.clip(clip_id)

    assert clip["transcript"] == "Make some noise."
    assert [word["raw_word"] for word in clip["words"]] == [
        "Make",
        "some",
        "noise.",
    ]
    sample = bank.make_sample(clip_id, 0, 2)
    assert sample["text"] == "Make some noise."


def test_module_help_uses_cli_parser():
    result = subprocess.run(
        [sys.executable, "-m", "wordbank.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage: wordbank" in result.stdout
    assert "serve" in result.stdout
    assert "ingest-batch" in result.stdout
    assert "ingest-youtube" in result.stdout
    assert "publish" in result.stdout
    assert "timing" in result.stdout


def test_api_routes(tmp_path, clip_file):
    bank = WordBank(tmp_path / "data", JSONTranscriber())
    client = TestClient(create_app(bank))
    assert client.get("/health").json() == {"status": "ok"}
    with clip_file.open("rb") as fh:
        transcript = json.dumps(
            {
                "words": [
                    {"text": "Hello,", "start": 0.05, "end": 0.35},
                    {"text": "world", "start": 0.45, "end": 0.8},
                    {"text": "hello", "start": 1.1, "end": 1.4},
                ]
            }
        )
        response = client.post(
            "/clips",
            files={"file": ("clip.wav", fh, "audio/wav")},
            data={"speaker": "Ada", "transcript": transcript},
        )
    assert response.status_code == 200
    clip = response.json()
    assert len(client.get(f"/clips/{clip['id']}").json()["words"]) == 3
    assert (
        client.get("/search?q=hello+world&speaker=Ada&limit=1").json()["hits"][0][
            "start"
        ]
        == 0.05
    )
    sample = client.post(
        "/samples",
        json={
            "clip_id": clip["id"],
            "start_word": 0,
            "end_word": 1,
            "tags": "intro,blue",
        },
    ).json()
    assert client.get("/samples?tag=blue").json()[0]["id"] == sample["id"]
    assert client.get("/samples?speaker=Ada").json()[0]["id"] == sample["id"]
    assert client.get(f"/samples/{sample['id']}/audio").status_code == 200
    timing = client.get(f"/clips/{clip['id']}/timing").json()
    assert timing["word_count"] == 3
    assert "whisperx" in timing["hint"]
    published = client.post(f"/samples/{sample['id']}/publish", json={}).json()
    assert Path(published["published_path"]).exists()
    recut = client.post(
        f"/samples/{sample['id']}/recut",
        json={"pad_before": -0.02, "pad_after": 0.05},
    ).json()
    assert recut["pad_before"] == pytest.approx(-0.02)
    assert client.delete(f"/samples/{sample['id']}").json() == {"deleted": True}
    assert client.get("/clips/999").status_code == 404
    assert client.get("/samples/999/audio").status_code == 404
    assert client.delete("/samples/999").status_code == 404
    settings = client.get("/settings").json()
    assert Path(settings["export_dir"]) == bank.export_dir
    assert Path(settings["data_dir"]) == bank.store.data_dir


def test_cli_serve_uses_data_dir(monkeypatch, tmp_path):
    captured = {}

    def fake_create_app(bank):
        captured["bank"] = bank
        return "app"

    def fake_run(app, host, port):
        captured["run"] = (app, host, port)

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setenv("WORDBANK_TRANSCRIBER", "json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "wordbank",
            "--data-dir",
            str(tmp_path / "custom"),
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9123",
        ],
    )

    cli.main()

    assert captured["bank"].store.data_dir == tmp_path / "custom"
    assert captured["run"] == ("app", "0.0.0.0", 9123)


def test_batch_ingest_folder(tmp_path, clip_file):
    folder = tmp_path / "batch"
    folder.mkdir()
    for name in ("a.wav", "b.wav"):
        target = folder / name
        target.write_bytes(clip_file.read_bytes())
        (folder / f"{Path(name).stem}.json").write_text(
            (clip_file.with_suffix(".json")).read_text()
        )
    bank = WordBank(tmp_path / "data", JSONTranscriber())
    clips = bank.ingest_batch(folder, speaker="MC")
    assert len(clips) == 2
    assert all(clip["speaker"] == "MC" for clip in clips)
    assert discover_audio_files(folder)[0].name == "a.wav"


def test_api_batch_upload(tmp_path, clip_file):
    from wordbank.models import Word

    class StubTranscriber:
        def transcribe(self, audio_path):
            return [
                Word("make", 0.05, 0.3, 0.9),
                Word("noise", 0.4, 0.7, 0.9),
            ]

    bank = WordBank(tmp_path / "data", StubTranscriber())
    client = TestClient(create_app(bank))
    with clip_file.open("rb") as one, clip_file.open("rb") as two:
        response = client.post(
            "/clips/batch",
            files=[
                ("files", ("one.wav", one, "audio/wav")),
                ("files", ("two.wav", two, "audio/wav")),
            ],
            data={"speaker": "Crowd"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["errors"] == []
    assert payload["clips"][0]["speaker"] == "Crowd"
    assert payload["clips"][0]["transcript"] == "make noise"


def test_publish_and_recut_preserve_word_indices(bank, clip_file, tmp_path):
    clip_id = bank.ingest(clip_file, speaker="Ada")
    sample = bank.make_sample(clip_id, 0, 1, label="make some noise")
    dest = tmp_path / "serato-watch"
    published = bank.publish_sample(sample["id"], dest)
    assert published.parent == dest
    assert published.name == "make_some_noise.wav"
    assert published.exists()

    again = bank.publish_sample(sample["id"], dest)
    assert again.name == "make_some_noise_2.wav"

    recut = bank.recut_sample(sample["id"], pad_before=-0.01, pad_after=0.2)
    assert recut["id"] == sample["id"]
    assert recut["start_word_index"] == 0
    assert recut["end_word_index"] == 1
    assert recut["pad_before"] == pytest.approx(-0.01)
    assert recut["pad_after"] == pytest.approx(0.2)

    published_on_make = bank.make_sample(
        clip_id,
        2,
        2,
        label="hello",
        publish=True,
        export_dir=dest,
    )
    assert Path(published_on_make["published_path"]).exists()


def test_timing_report_flags_gaps(bank, clip_file):
    clip_id = bank.ingest(clip_file, speaker="Ada")
    report = bank.timing_report(clip_id)
    assert report["word_count"] == 3
    assert report["average_confidence"] == pytest.approx(0.866666, abs=0.01)
    assert any(gap["gap"] > 0.2 for gap in report["suspicious_gaps"])


def test_sanitize_label_filename(tmp_path):
    assert sanitize_label_filename("Make some noise!") == "Make_some_noise!"
    assert sanitize_label_filename("a/b:c") == "abc"
    assert sanitize_label_filename("   ") == "sample"
    path = unique_path(tmp_path, "x")
    assert path == tmp_path / "x.wav"
    path.write_text("a")
    assert unique_path(tmp_path, "x").name == "x_2.wav"


def test_default_data_dir_is_peteedoo_samples(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("WORDBANK_DATA_DIR", raising=False)
    monkeypatch.delenv("WORDBANK_EXPORT_DIR", raising=False)
    assert default_data_dir() == tmp_path / "peteedoo" / "samples"
    bank = WordBank(transcriber=JSONTranscriber())
    assert bank.store.data_dir == (tmp_path / "peteedoo" / "samples").resolve()
    assert bank.export_dir == bank.store.data_dir
    assert (bank.store.data_dir / "wordbank.sqlite3").exists()


def test_resolve_dir_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_dir("~/peteedoo/samples", Path("/fallback")) == (
        tmp_path / "peteedoo" / "samples"
    ).resolve()


def test_youtube_timestamp_window():
    from wordbank.youtube import parse_timestamp, resolve_window

    assert parse_timestamp("1:05") == 65
    assert parse_timestamp("1:02:03") == 3723
    assert parse_timestamp(12.5) == 12.5
    assert resolve_window(None, None, None) == (0.0, 30.0)
    assert resolve_window("1:00", None, None) == (60.0, 90.0)
    assert resolve_window("10", "25", None) == (10.0, 25.0)
    assert resolve_window(5, None, 12) == (5.0, 17.0)
    with pytest.raises(ValueError, match="after start"):
        resolve_window(10, 10, None)
    with pytest.raises(ValueError, match="max"):
        resolve_window(0, 30 * 60 + 1, None)


def test_ingest_youtube_uses_fetcher(tmp_path, clip_file):
    from wordbank.models import Word

    class StubTranscriber:
        def transcribe(self, audio_path):
            return [
                Word("drop", 0.1, 0.4),
                Word("the", 0.45, 0.6),
                Word("beat", 0.7, 1.0),
            ]

    bank = WordBank(tmp_path / "data", StubTranscriber())
    captured = {}

    def fake_fetch(url, destination, start, end):
        captured["args"] = (url, start, end)
        destination.write_bytes(clip_file.read_bytes())
        return {
            "title": "Drop The Beat",
            "video_id": "abc123",
            "url": url,
            "filename": "Drop_The_Beat_5-35.wav",
            "start": start,
            "end": end,
        }

    clip_id = bank.ingest_youtube(
        "https://youtu.be/abc123",
        speaker="MC",
        start=5,
        duration_seconds=30,
        fetcher=fake_fetch,
    )
    clip = bank.store.clip(clip_id)
    assert captured["args"] == ("https://youtu.be/abc123", 5.0, 35.0)
    assert clip["speaker"] == "MC"
    assert clip["original_filename"] == "Drop_The_Beat_5-35.wav"
    assert clip["transcript"] == "drop the beat"


def test_api_youtube_ingest(tmp_path, clip_file):
    from wordbank.models import Word

    class StubTranscriber:
        def transcribe(self, audio_path):
            return [Word("yeah", 0.1, 0.3)]

    bank = WordBank(tmp_path / "data", StubTranscriber())

    def fake_fetch(url, destination, start, end):
        destination.write_bytes(clip_file.read_bytes())
        return {
            "title": "Yeah",
            "video_id": "xyz",
            "url": url,
            "filename": "Yeah_0-30.wav",
            "start": start,
            "end": end,
        }

    def ingest_with_fake(
        url,
        speaker=None,
        start=None,
        end=None,
        duration_seconds=None,
        fetcher=None,
    ):
        return WordBank.ingest_youtube(
            bank,
            url,
            speaker=speaker,
            start=start,
            end=end,
            duration_seconds=duration_seconds,
            fetcher=fake_fetch,
        )

    bank.ingest_youtube = ingest_with_fake  # type: ignore[method-assign]
    client = TestClient(create_app(bank))
    response = client.post(
        "/clips/youtube",
        json={"url": "https://www.youtube.com/watch?v=xyz", "speaker": "Crowd"},
    )
    assert response.status_code == 200
    assert response.json()["speaker"] == "Crowd"
    assert response.json()["original_filename"] == "Yeah_0-30.wav"
    assert client.post("/clips/youtube", json={}).status_code == 400
