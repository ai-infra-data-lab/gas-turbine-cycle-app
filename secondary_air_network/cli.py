"""Batch solve or grid diagnostic report. Invalid requests never write output.

A grid report intentionally records failed levels, writes diagnostics and exits 2.
"""
import argparse
import json
from pathlib import Path
from engine import CalculationError, solve
from model import ModelError
from grid_study import grid_study


def main():
    p = argparse.ArgumentParser(description="Steady secondary-air network")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--grid-study", action="store_true", help="基準・2倍・4倍の分割数依存性を比較")
    p.add_argument("--relative-percent", type=float, default=.1, help="分割比較の相対許容値 %% (default: 0.1)")
    args = p.parse_args()
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8-sig"))
        result = grid_study(raw, args.relative_percent) if args.grid_study else solve(raw)
    except (ValueError, ModelError, CalculationError) as exc:
        p.exit(2, f"計算失敗: {exc}\n")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": result["status"], "assessment": result["assessment"]} if args.grid_study else result["summary"], ensure_ascii=False, indent=2))
    if args.grid_study and result["status"] == "incomplete":
        p.exit(2, "一部の分割で計算未成立。診断レポートを出力しました。\n")


if __name__ == "__main__":
    main()
