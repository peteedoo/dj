import argparse
import json
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

    search = subcommands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--speaker")
    search.add_argument("--limit", type=int, default=50)

    export = subcommands.add_parser("export")
    export.add_argument("clip_id", type=int)
    export.add_argument("start_word", type=int)
    export.add_argument("end_word", type=int)
    export.add_argument("--label", default="")
    export.add_argument(
        "--publish",
        action="store_true",
        help="Also copy the WAV into the DJ export folder",
    )

    publish = subcommands.add_parser(
        "publish",
        help="Copy a sample into the DJ watch folder, named by label",
    )
    publish.add_argument("sample_id", type=int)
    publish.add_argument(
        "--dest",
        default=None,
        help="Override export directory for this publish",
    )

    timing = subcommands.add_parser(
        "timing",
        help="Report word-edge confidence / gaps for a clip",
    )
    timing.add_argument("clip_id", type=int)

    serve = subcommands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bank = WordBank(args.data_dir, export_dir=args.export_dir)

    if args.command == "serve":
        uvicorn.run(
            create_app(bank),
            host=args.host,
            port=args.port,
        )
        return

    result: Any
    if args.command == "ingest":
        clip_id = bank.ingest(Path(args.audio), speaker=args.speaker)
        result = bank.store.clip(clip_id)
    elif args.command == "ingest-batch":
        result = bank.ingest_batch(Path(args.folder), speaker=args.speaker)
    elif args.command == "search":
        result = bank.store.search(args.query, args.speaker, args.limit)
    elif args.command == "publish":
        path = bank.publish_sample(args.sample_id, args.dest)
        result = {"published_path": str(path)}
    elif args.command == "timing":
        result = bank.timing_report(args.clip_id)
    else:
        result = bank.make_sample(
            args.clip_id,
            args.start_word,
            args.end_word,
            args.label,
            publish=args.publish,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
