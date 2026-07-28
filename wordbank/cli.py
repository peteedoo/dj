import argparse
import json
from pathlib import Path
from typing import Any

from .api import create_app
from .service import WordBank


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wordbank")
    parser.add_argument("--data-dir", default=None)
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest")
    ingest.add_argument("audio")
    ingest.add_argument("--speaker")

    search = subcommands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--speaker")
    search.add_argument("--limit", type=int, default=50)

    export = subcommands.add_parser("export")
    export.add_argument("clip_id", type=int)
    export.add_argument("start_word", type=int)
    export.add_argument("end_word", type=int)
    export.add_argument("--label", default="")

    serve = subcommands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bank = WordBank(args.data_dir)

    if args.command == "serve":
        import uvicorn

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
    elif args.command == "search":
        result = bank.store.search(args.query, args.speaker, args.limit)
    else:
        result = bank.make_sample(
            args.clip_id,
            args.start_word,
            args.end_word,
            args.label,
        )
    print(json.dumps(result, indent=2))
