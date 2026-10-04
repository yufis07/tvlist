"""Entry point.

    python -m tvlist                      # open the window
    python -m tvlist --cli -f "D:/TV"     # command line (asks for login)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import os
import sys
from pathlib import Path

from . import config
from .pogdesign import LoginError, PogError
from .sync import Options, load_aliases, run_sync


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="tvlist",
        description="Tick local TV episodes as watched on PoGDesign and list missing ones.",
    )
    ap.add_argument("--cli", action="store_true", help="run without the window")
    ap.add_argument("-f", "--folder", action="append", default=[],
                    help="TV folder to scan (repeatable)")
    ap.add_argument("-e", "--email", default=os.environ.get("POG_EMAIL"),
                    help="PoGDesign e-mail (or set POG_EMAIL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview only; don't tick anything on the site")
    ap.add_argument("--only-local", action="store_true",
                    help="don't check filtered shows that have no files on disk")
    ap.add_argument("--specials", action="store_true", help="include season 0")
    ap.add_argument("--hide-watched-missing", action="store_true",
                    help="don't list missing episodes already marked watched on the site")
    ap.add_argument("--aliases", default=str(config.aliases_path()),
                    help="JSON file mapping local show names to PoGDesign names")
    ap.add_argument("-o", "--output", help="write the report to this file")
    args = ap.parse_args(argv)

    if not args.folder:
        ap.error("give at least one --folder")
    email = args.email or input("PoGDesign e-mail: ").strip()
    password = os.environ.get("POG_PASSWORD") or getpass.getpass("PoGDesign password: ")

    opts = Options(
        folders=args.folder,
        email=email,
        password=password,
        dry_run=args.dry_run,
        include_tracked=not args.only_local,
        include_specials=args.specials,
        hide_watched_missing=args.hide_watched_missing,
        aliases=load_aliases(args.aliases),
    )
    try:
        rep = run_sync(opts)
    except LoginError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except PogError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    text = rep.to_text()
    print("\n" + text)
    out = args.output or f"tvlist-report-{_dt.datetime.now():%Y%m%d-%H%M}.txt"
    Path(out).write_text(text, encoding="utf-8")
    print(f"Report saved to {out}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv == ["--gui"]:
        try:
            from .gui import main as gui_main
        except ImportError as e:  # Tk missing (some minimal Linux installs)
            print(f"Window mode unavailable ({e}); use --cli. See --help.", file=sys.stderr)
            return 1
        gui_main()
        return 0
    return cli(argv)


if __name__ == "__main__":
    sys.exit(main())
