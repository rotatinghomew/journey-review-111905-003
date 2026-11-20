#!/usr/bin/env python3
"""本地审议入口：python3 -I run_review.py --fixtures fixtures --out reports/review.md"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from rollout_review.report import build_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="自助办理改版站点组合审议（仅本地记录）")
    parser.add_argument("--fixtures", default="fixtures")
    parser.add_argument("--out", default=None, help="输出 Markdown 路径；缺省打印到标准输出")
    args = parser.parse_args()
    report = build_report(args.fixtures)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"已写出 {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
