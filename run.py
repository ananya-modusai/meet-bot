#!/usr/bin/env python3
"""
Unified entry point. Routes to the correct platform implementation.

Usage:
  python run.py --platform recall --meeting "https://zoom.us/j/..." --doc doc.pdf
  python run.py --platform zoom   --meeting "https://zoom.us/j/..."
  python run.py --platform meet   --meeting "https://meet.google.com/..." --doc doc.pdf
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="Meeting AI Agent")
    parser.add_argument(
        "--platform",
        choices=["recall", "zoom", "meet"],
        required=True,
        help="Which platform / approach to use",
    )
    parser.add_argument("--meeting", required=True, help="Meeting URL")
    parser.add_argument("--doc", default="", help="Path to PDF document (recall/meet only)")
    args = parser.parse_args()

    if args.platform == "recall":
        cmd = [sys.executable, str(HERE / "recall" / "agent.py"), "--meeting", args.meeting]
        if args.doc:
            cmd += ["--doc", args.doc]

    elif args.platform == "zoom":
        cmd = [sys.executable, str(HERE / "zoom" / "agent.py"), "--meeting", args.meeting]

    elif args.platform == "meet":
        cmd = [sys.executable, str(HERE / "agent.py"), "--meet", args.meeting]
        if args.doc:
            cmd += ["--doc", args.doc]

    subprocess.run(cmd, cwd=str(HERE))


if __name__ == "__main__":
    main()
