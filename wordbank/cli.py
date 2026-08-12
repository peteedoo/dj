import argparse
import json
import sys
from pathlib import Path
from typing import Any

import uvicorn

from .api import create_app
from .service import WordBank


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wordbank")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument(
        "--export-dir",
        default=None,
        help="Serato/Rekordbox watch folder for published samples",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest")
    ingest.add_argument("audio")
    ingest.add_argument("--speaker")

    batch = subcommands.add_parser(
        "ingest-batch",
        help="Ingest every audio file in a folder under one speaker label",
    )
    batch.add_argument("folder")
    batch.add_argument("--speaker", required=True)

    youtube = subcommands.add_parser(
        "ingest-youtube",
        help="Download a YouTube time window (default 30s) and ingest it",
    )
    youtube.add_argument("url")
    youtube.add_argument("--speaker")
    youtube.add_argument("--start", default="0")
    youtube.add_argument("--end", default=None)
    youtube.add_argument("--duration", default=None)

    search = subcommands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--speaker")
    search.add_argument("--limit", type=int, default=50)

    export = subcommands.add_parser("export")
    export.add_argument("clip_id", type=int)
    export.add_argument("start_word", type=int)
    export.add_argument("end_word", type=int)
    export.add_argument("--label", default="")
    export.add_argument("--pad-before", type=float, default=-0.08)
    export.add_argument("--pad-after", type=float, default=0.12)
    export.add_argument("--tags", default="")
    export.add_argument("--publish", action="store_true")

    expand = subcommands.add_parser("expand")
    expand.add_argument("sample_id", type=int)
    expand.add_argument("--before", type=int, default=0)
    expand.add_argument("--after", type=int, default=0)

    recut = subcommands.add_parser("recut")
    recut.add_argument("sample_id", type=int)
    recut.add_argument("--pad-before", type=float, default=None)
    recut.add_argument("--pad-after", type=float, default=None)

    publish = subcommands.add_parser("publish")
    publish.add_argument("sample_id", type=int)
    publish.add_argument("--dest", default=None)

    timing = subcommands.add_parser("timing")
    timing.add_argument("clip_id", type=int)

    subcommands.add_parser("clips", help="List clips")
    samples = subcommands.add_parser("samples", help="List samples")
    samples.add_argument("--q", default=None)
    samples.add_argument("--speaker", default=None)
    samples.add_argument("--tag", default=None)

    delete_sample = subcommands.add_parser("delete-sample")
    delete_sample.add_argument("sample_id", type=int)

    delete_clip = subcommands.add_parser("delete-clip")
    delete_clip.add_argument("clip_id", type=int)

    serve = subcommands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding to a non-loopback address (no auth)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bank = WordBank(args.data_dir, export_dir=args.export_dir)

    if args.command == "serve":
        host = args.host
        if (
            host not in {"127.0.0.1", "localhost", "::1"}
            and not args.allow_remote
        ):
            print(
                "Refusing to bind a non-loopback host without --allow-remote "
                "(wordbank has no auth).",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(
            f"wordbank data_dir={bank.store.data_dir} "
            f"export_dir={bank.export_dir}",
            file=sys.stderr,
        )
        uvicorn.run(
            create_app(bank),
            host=host,
            port=args.port,
        )
        return

    result: Any
    try:
        if args.command == "ingest":
            clip_id = bank.ingest(Path(args.audio), speaker=args.speaker)
            result = bank.store.clip(clip_id)
        elif args.command == "ingest-batch":
            result = bank.ingest_batch(Path(args.folder), speaker=args.speaker)
        elif args.command == "ingest-youtube":
            clip_id = bank.ingest_youtube(
                args.url,
                speaker=args.speaker,
                start=args.start,
                end=args.end,
                duration_seconds=args.duration,
            )
            result = bank.store.clip(clip_id)
        elif args.command == "search":
            result = bank.store.search(args.query, args.speaker, args.limit)
        elif args.command == "publish":
            path = bank.publish_sample(
                args.sample_id,
                args.dest,
                restrict=False,
            )
            result = {"published_path": str(path)}
        elif args.command == "timing":
            result = bank.timing_report(args.clip_id)
        elif args.command == "expand":
            result = bank.expand_sample(args.sample_id, args.before, args.after)
        elif args.command == "recut":
            result = bank.recut_sample(
                args.sample_id,
                args.pad_before,
                args.pad_after,
            )
        elif args.command == "clips":
            result = bank.store.clips()
        elif args.command == "samples":
            result = bank.store.samples(args.q, args.speaker, args.tag)
        elif args.command == "delete-sample":
            ok = bank.store.delete_sample(args.sample_id)
            if not ok:
                raise ValueError("Sample not found")
            result = {"deleted": True}
        elif args.command == "delete-clip":
            ok = bank.store.delete_clip(args.clip_id)
            if not ok:
                raise ValueError("Clip not found")
            result = {"deleted": True}
        else:
            result = bank.make_sample(
                args.clip_id,
                args.start_word,
                args.end_word,
                args.label,
                pad_before=args.pad_before,
                pad_after=args.pad_after,
                tags=args.tags,
                publish=args.publish,
            )
    except Exception as exc:  # noqa: BLE001 - CLI exit non-zero
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
