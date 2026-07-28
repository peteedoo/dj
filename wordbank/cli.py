import argparse
import json
from pathlib import Path

from .api import create_app
from .service import WordBank


def main():
    parser = argparse.ArgumentParser(prog="wordbank")
    parser.add_argument("--data-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest"); ingest.add_argument("audio"); ingest.add_argument("--speaker")
    search = sub.add_parser("search"); search.add_argument("query"); search.add_argument("--speaker")
    export = sub.add_parser("export"); export.add_argument("clip_id", type=int); export.add_argument("start_word", type=int); export.add_argument("end_word", type=int); export.add_argument("--label", default="")
    serve = sub.add_parser("serve"); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return
    bank = WordBank(args.data_dir)
    if args.command == "ingest": result = bank.store.clip(bank.ingest(Path(args.audio), speaker=args.speaker))
    elif args.command == "search": result = bank.store.search(args.query, args.speaker)
    else: result = bank.make_sample(args.clip_id, args.start_word, args.end_word, args.label)
    print(json.dumps(result, indent=2))
