"""Command-line adapter, usable without the local web server."""
import argparse
import json
from pathlib import Path

if __package__:
    from .engine import run_case
    from .model import default_input
    from .server import read_input_file
else:
    from engine import run_case
    from model import default_input
    from server import read_input_file


def main():
    parser = argparse.ArgumentParser(description="Simple-cycle, single-shaft design-point calculation")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else default_input()
    case = run_case(read_input_file(raw))
    args.output.write_text(json.dumps(case, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Result: {args.output.resolve()}")


if __name__ == "__main__":
    main()
