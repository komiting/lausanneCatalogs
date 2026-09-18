"""Command line interface.

  python -m scraper run    [--stores migros,coop] [--date 2026-09-16] [--replay rec.json ...]
  python -m scraper build
  python -m scraper all    (run + build)
  python -m scraper check  (quick connectivity test)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from .basket import load_basket
from .build import build
from .http import LiveHttp, RecordingHttp, ReplayHttp
from .run import live_http_factory, run
from .stores import STORES

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scraper", description="Lausanne price tracker")
    ap.add_argument("command", choices=["run", "build", "all", "check"])
    ap.add_argument("--stores", help="comma separated: " + ",".join(STORES))
    ap.add_argument("--date", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--site", default=str(ROOT / "site"))
    ap.add_argument("--replay", nargs="*", help="recorded responses to use instead of the network")
    ap.add_argument("--record", help="save all live responses into this folder")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    stores = args.stores.split(",") if args.stores else list(STORES)
    unknown = [s for s in stores if s not in STORES]
    if unknown:
        ap.error(f"unknown store(s): {unknown}")
    today = dt.date.fromisoformat(args.date) if args.date else None

    if args.command == "check":
        return check(stores)

    if args.command in ("run", "all"):
        recorders: dict[str, RecordingHttp] = {}
        if args.replay:
            replay = ReplayHttp.from_files([Path(p) for p in args.replay], strict=False)
            factory = lambda cls: replay  # noqa: E731
        elif args.record:
            def factory(cls):
                rec = RecordingHttp(live_http_factory(cls))
                recorders[cls.key] = rec
                return rec
        else:
            factory = live_http_factory
        summary = run(stores, today, Path(args.data), factory, load_basket())
        for key, rec in recorders.items():
            rec.save(Path(args.record) / f"{key}.json")
        print(json.dumps(summary, ensure_ascii=False))
        if args.replay and replay.missing:
            logging.warning("%d requests had no recording, e.g. %s", len(replay.missing), replay.missing[:3])
        if all(v == "error" for v in summary["stores"].values()):
            return 1
    if args.command in ("build", "all"):
        sizes = build(Path(args.data), Path(args.site))
        print("site data:", ", ".join(f"{k} {v // 1024} KB" for k, v in sizes.items()))
    return 0


def check(stores: list[str]) -> int:
    ok = True
    for key in stores:
        cls = STORES[key]
        store = cls(LiveHttp(impersonate=cls.impersonate, headers=cls.headers, delay=0.2, retries=1))
        try:
            if cls.mode == "search":
                n = len(store.search("lait"))
            else:
                n = len(store.promotions())
            print(f"{cls.name:8} OK   ({n} proizvoda)")
        except Exception as exc:
            ok = False
            print(f"{cls.name:8} GREŠKA {type(exc).__name__}: {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
