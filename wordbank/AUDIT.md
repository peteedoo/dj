# Wordbank audit — 100 functional fixes / improvements

Nothing cosmetic. Each item is a real bug, data-integrity hole, correctness
failure, reliability gap, or missing capability that blocks DJ use.

Status key: `[x]` fixed in this pass · `[ ]` still open

## Search & indexing

1. [x] Phrase search applies `LIMIT` to first-token candidates before phrase match — common words (`the`, `make`) starve real multi-word hits.
2. [x] Add regression test for phrase+limit starvation.
3. [x] Drop empty / punctuation-only normalized tokens so they don’t break consecutive phrase alignment.
4. [ ] Multi-token search does N+1 queries per candidate — batch or join for large banks.
5. [ ] Search has no offset/cursor — UI can’t page past `limit` without changing semantics.
6. [x] Speaker filter is case/whitespace exact while words are normalized — normalize speaker on write+query.
7. [x] Sample library `LIKE` treats `%`/`_` in query/tag as wildcards — escape metacharacters.
8. [x] Tags with spaces / inconsistent separators fail the comma-wrap filter — normalize tags on write.
9. [ ] No FTS/trigram index — large libraries will get slow on first-token scans.
10. [ ] No clip-level “reindex” after swapping a better transcriber — must re-ingest duplicates.

## SQLite / storage integrity

11. [x] No WAL / busy timeout — concurrent UI+CLI writers hit `database is locked`.
12. [x] `delete_sample` unlinks WAV before DB delete — crash leaves dangling rows.
13. [x] Published DJ copies are untracked — delete/recut leave stale Serato files.
14. [x] Persist `published_path` and refresh/remove it on publish/recut/delete.
15. [ ] Absolute `audio_path` / `exported_path` break if `data_dir` is moved — store paths relative to root.
16. [x] FileResponse should 404 when the on-disk file is missing.
17. [ ] No foreign-key cascade test for deleting clips with samples.
18. [x] Add `DELETE /clips/{id}` that removes words, samples, and audio files in one flow.
19. [ ] No content-hash dedupe on ingest — re-uploads duplicate the bank.
20. [ ] No backup/vacuum/integrity-check CLI for the SQLite file.

## Sample cut math & expand

21. [x] Inverted pads produce `end < start`; ffmpeg exports a 1ms garbage slice from the wrong place.
22. [x] Reject negative expand `before`/`after` (API allowed shrink via negatives).
23. [x] `expand(0,0)` still duplicated the sample — no-op instead.
24. [ ] Expand does not optionally re-publish the new sample when the parent was published.
25. [x] Recut must refresh the tracked published file in place (overwrite), not create `label_2.wav`.
26. [ ] Pad defaults (−80/+120ms) aren’t clamped relative to neighboring word boundaries (can bleed into adjacent words).
27. [ ] No preview-only cut (audition pads without writing a sample row).
28. [ ] `export_slice` always re-encodes PCM even when source is already WAV — keep format option for DJ bit-depth.
29. [x] Surface ffmpeg/ffprobe stderr in raised errors instead of opaque `CalledProcessError`.
30. [x] Write sample WAVs to a temp path and replace on success — avoid partial files on failure.

## Publish / export paths

31. [x] Default publish into `published/` under the data dir so Serato isn’t pointed at sqlite/temps.
32. [x] Reject client `export_dir` outside the configured export root (path traversal / arbitrary write).
33. [x] Put upload/YouTube temps under `.tmp/` so watch folders don’t index them.
34. [x] Stricter label→filename sanitization (charset, length, reserved names).
35. [x] `unique_path` TOCTOU — create with exclusive create / retry.
36. [ ] Optional hardlink/symlink publish for SSD space.
37. [ ] Publish should preserve broadcast WAV metadata (origin URL, timestamps) in a sidecar `.txt`/cue for crate notes.
38. [ ] No “unpublish” that removes the DJ copy but keeps the library sample.
39. [ ] No conflict policy when label already exists (overwrite vs suffix) — make it explicit.
40. [ ] Export naming can’t include speaker/prefix templates (`{speaker}_{label}.wav`).

## Ingest reliability

41. [x] Ingest leaves orphan audio if transcription/DB insert fails — clean up destination on error.
42. [x] Upload temp names collide under concurrent same-basename uploads — use unique temps.
43. [x] Stream uploads to disk with a max size instead of `file.read()` into RAM.
44. [x] Reject non-audio extensions before ffmpeg/transcribe.
45. [x] Service `ingest_batch` aborts on first failure — return per-file errors like the API.
46. [ ] Missing JSON sidecar under `json` transcriber yields cryptic errors — clearer message.
47. [x] Validate JSON sidecar schema (list of word objects, start<end, float confidence).
48. [x] Cache FasterWhisper / WhisperX models on the transcriber instance (reload-per-call is unusable).
49. [ ] WhisperX silently drops words missing timings — report drop rate / fail if high.
50. [ ] No progress events for long ingest (batch/YouTube/whisper) — CLI/API hang with no heartbeat.

## YouTube import

51. [x] `looks_like_youtube` was unused — reject non-YouTube URLs before yt-dlp.
52. [x] Error when both `end` and `duration` are provided (was silent ignore).
53. [x] Subprocess timeouts for yt-dlp/ffmpeg so workers can’t hang forever.
54. [x] Prefer `clip.wav` / known audio extensions over alphabetical `clip.*`.
55. [x] ffprobe downloaded section; warn/fail if duration is far from the requested window.
56. [x] Run YouTube ingest off the event loop (`to_thread`) so the API stays responsive.
57. [ ] Cancelable YouTube jobs (job id + abort) for the UI Cancel button.
58. [ ] Playlist URLs can still surprise despite `--no-playlist` on some extractors — harden.
59. [ ] Age-gated / bot-check failures need a clearer “update yt-dlp / cookies” hint.
60. [ ] Store source URL + start/end on the clip row for provenance/re-cut.

## API contract & security

61. [x] Expand mapped all `ValueError` to 404 — distinguish 400 vs 404.
62. [x] Bound search `limit` (1..500).
63. [x] Warn / refuse non-loopback bind without an explicit `--allow-remote` flag.
64. [ ] Optional shared-secret / token auth for LAN use.
65. [x] Package `static/index.html` in wheel package-data so installed `GET /` works.
66. [ ] Pydantic request models for all JSON bodies (types, null handling).
67. [x] Batch all-failed should not look like success — non-2xx or explicit `ok:false`.
68. [ ] Rate-limit YouTube + upload endpoints.
69. [ ] Health check should verify ffmpeg/ffprobe/yt-dlp/db writable.
70. [ ] OpenAPI examples for the DJ workflows (ingest → search → sample → publish).

## CLI gaps that block real use

71. [x] Add `clips` / `samples` list commands.
72. [x] Add `delete-sample` and `delete-clip`.
73. [x] Add `expand` and `recut` subcommands.
74. [x] `export` gains `--pad-before` / `--pad-after` / `--tags`.
75. [ ] `doctor` command for deps + data-dir permissions + WAL status.
76. [ ] Shell completion for speakers / recent clip ids.
77. [ ] `serve` should print the resolved data/export paths on boot.
78. [ ] Non-zero exit codes when CLI operations fail (not just JSON error text).
79. [ ] `search --jsonl` streaming for piping into other tools.
80. [ ] `publish --overwrite` flag.

## UI correctness (functional, not cosmetic)

81. [x] `makeSample` / `expand` / `publish` / `search` ignore `response.ok` — silent false success.
82. [x] Sample library XSS via `innerHTML` with labels — use text nodes.
83. [x] Waveform load race on fast j/k — generation token / ignore stale.
84. [x] Enter/Space must audition the current selection (incl. pads), not only original hit start.
85. [x] Enter/Space works with a selection even when there are no search hits.
86. [x] Esc clears hits fully, not just the highlight index.
87. [x] Guard zero-duration clips in waveform math.
88. [x] Label expand inputs as “words before/after” (not pad seconds).
89. [x] Delete sample control wired to `DELETE /samples/{id}`.
90. [x] Recut from pad fields on the library row.
91. [x] Cache-bust sample/clip audio URLs after mutate; `Cache-Control: no-store` on audio.
92. [x] YouTube import disables the button while in flight and surfaces errors.
93. [ ] AbortController + Cancel for YouTube import.
94. [ ] Warn when multi-file selected but single INGEST is clicked.
95. [ ] Share one blob fetch between `<audio>` and waveform decode.
96. [ ] Resume `AudioContext` on first gesture.
97. [ ] Show timing-report summary (not only raw JSON) with a clear “try whisperx” CTA when gaps are bad.
98. [ ] Keyboard delete / publish shortcuts for the focused sample.
99. [ ] Offline/server-down detection with a reconnect banner (search/ingest otherwise look “empty”).
100. [x] Settings refresh after publish so the export path shown stays truthful.

---

Highest-impact cluster addressed first: #1 search starvation, SQLite/publish integrity,
pad validation, YouTube hardening, API safety, and UI silent-failure / audition bugs.
