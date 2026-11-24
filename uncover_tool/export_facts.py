"""Export every selected instance's uncovered points without loading NPI."""
import argparse
import gzip
from pathlib import Path

from .common import ToolError, require
from .facts import FactsReader, packed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path, help="published coverage result directory")
    parser.add_argument("--kind", default="holes", help="only holes is supported; difference exports were removed")
    parser.add_argument("--out", required=True, type=Path, help="new JSONL or .jsonl.gz file")
    args = parser.parse_args(argv)
    require(args.kind == "holes", "Difference analysis was removed; use --kind holes for all instance uncovered points")
    require(args.result.is_dir(), "Result directory does not exist")
    require(not args.out.exists(), "Export output already exists; choose a new file")
    reader = FactsReader(args.result / "coverage_facts.sqlite")
    count = 0
    try:
        opener = gzip.open if args.out.suffix == ".gz" else open
        with opener(args.out, "xt", encoding="utf-8") as output:
            for row in reader.details(args.kind):
                output.write(packed(row) + "\n")
                count += 1
    finally:
        reader.close()
    print(f"Exported {count} records to {args.out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ToolError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
