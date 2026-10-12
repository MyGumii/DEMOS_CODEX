"""Host-only export. Run on the same host/key; never an HTTP download endpoint."""
import argparse
from collections import Counter
import json
from pathlib import Path

from code_assist_v1.auth import ensure_secret_key
from code_assist_v1.notion_notes_comparison import ComparisonStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="New private JSONL file. Contains sensitive originals and both summaries.")
    args = parser.parse_args()
    if not args.db.is_absolute() or not args.db.is_file():
        parser.error("--db must name an existing absolute host database path")
    if args.output and (not args.output.is_absolute() or args.output.exists()):
        parser.error("--output must be a new absolute private file path")
    store = ComparisonStore(args.db, ensure_secret_key())
    records = list(store.host_records())
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"count": len(records), "states": dict(Counter(record["state"] for record in records)), "exported": bool(args.output)}))


if __name__ == "__main__":
    main()
