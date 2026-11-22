"""kassist command line."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import browser, catalog, engine, planner
from .config import load
from .state import ItemState, Store

from .catalog import TEMPLATE  # noqa: E402  (one template for `new` and `import`)


def _status(settings, store: Store) -> None:
    today = date.today()
    items = {i.key: i for i in store.items()}
    for s in catalog.scan(settings.source_dir, settings.catalog()):
        name = s.folder.name
        if s.error:
            print(f"❌ {name:26} {s.error}")
        elif s.listing is None:
            print(f"–  {name:26} {'draft available: kassist import' if s.importable else 'no listing.yaml'}")
        else:
            item = items.get(name, ItemState(name))
            acts = planner.plan_item(s.listing, item, today)
            nxt = "; ".join(f"{a.kind} {a.price}" for a in acts) or "-"
            mark = "✅" if s.listing.approved else "⏸"
            price = f"{item.price}" if item.price is not None else f"({s.listing.schedule.start})"
            print(f"{mark} {name:26} {item.status:9} {price:>6} EUR  → {s.listing.schedule.floor:>4} (min {s.listing.min_sale})  next: {nxt}"
                  + (f"  ⚠ {item.last_error[:60]}" if item.last_error else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kassist", description="Kleinanzeigen assistant")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("browser", help="start the persistent browser (log in there once)")
    sub.add_parser("status", help="folders, state, next action")
    imp = sub.add_parser("import", help="create listing.yaml from *_inserat_*.md drafts")
    imp.add_argument("folders", nargs="*", help="folder names (default: all importable)")
    nw = sub.add_parser("new", help="write an empty listing.yaml template into a folder")
    nw.add_argument("folder")
    t = sub.add_parser("tick", help="sync, plan and execute due actions")
    t.add_argument("--dry-run", action="store_true")
    i = sub.add_parser("inbox", help="poll messages, notify, draft replies")
    i.add_argument("--force", action="store_true")
    i.add_argument("--no-drafts", action="store_true")
    for what in engine.MANUAL:
        m = sub.add_parser(what, help=f"{what} an item")
        m.add_argument("folder")
    pr = sub.add_parser("prices", help="price overview from similar Kleinanzeigen ads")
    pr.add_argument("folder", nargs="?")
    pr.add_argument("--query", help="search words (default: from the listing title)")
    pr.add_argument("--ai", action="store_true", help="let the AI cross-check condition and contents")
    g = sub.add_parser("gui", help="open the GUI as an app window")
    g.add_argument("--no-window", action="store_true", help="only serve on 127.0.0.1, no window")
    g.add_argument("--background", action="store_true", help="start in the tray without opening the window")
    sub.add_parser("panel", help="serve the GUI without opening a window (same as gui --no-window)")
    sub.add_parser("install-desktop", help="Linux: add kassist to the application menu")
    sub.add_parser("sync", help="read the account's ads and update local state")
    args = ap.parse_args(argv)

    settings = load()
    store = Store(settings.db_path)
    try:
        if args.cmd == "browser":
            print("started" if browser.start(settings) else "already running")
        elif args.cmd == "status":
            _status(settings, store)
        elif args.cmd == "import":
            blocks = settings.catalog().text_blocks
            for s in catalog.scan(settings.source_dir):
                if s.importable and (not args.folders or s.folder.name in args.folders):
                    try:
                        print(f"✅ {catalog.import_draft(s.folder, s.importable, blocks)}")
                    except (ValueError, FileExistsError) as exc:
                        print(f"❌ {s.folder.name}: {exc}")
        elif args.cmd == "new":
            target = settings.source_dir / args.folder / catalog.LISTING_FILE
            if target.parent.parent != settings.source_dir or not target.parent.is_dir():
                raise ValueError(f"no folder {args.folder} in {settings.source_dir}")
            if target.exists():
                raise ValueError(f"{target} exists")
            target.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"✅ {target}")
        elif args.cmd == "tick":
            for line in engine.tick(settings, store, dry_run=args.dry_run) or ["nothing due"]:
                print(line)
        elif args.cmd == "sync":
            with engine.browser_lock(settings):
                online = engine.sync(settings, store, date.today())
            print(f"{len(online)} ads in the account")
        elif args.cmd == "inbox":
            changed = engine.poll_inbox(settings, store, force=args.force, with_drafts=not args.no_drafts)
            print(f"{len(changed)} conversation(s) updated")
        elif args.cmd == "prices":
            if not args.folder and not args.query:
                raise ValueError("give a folder or --query")
            s = engine.price_check(settings, store, key=args.folder, query=args.query, ai=args.ai)
            if not s.get("used"):
                print(f"„{s['query']}“: {s['found']} results, no usable prices")
            else:
                print(f"„{s['query']}“: {s['used']} of {s['found']} offers (outliers {s['outliers']})\n"
                      f"  min {s['min']} · 25% {s['p25']} · median {s['median']} · 75% {s['p75']} · max {s['max']} EUR")
            a = s.get("ai")
            if a:
                if a.get("comparable"):
                    print(f"  AI: {a['comparable']} comparable, adjusted min {a['min']} · median {a['median']} · max {a['max']} EUR")
                else:
                    print("  AI: no comparable offers")
                if a.get("suggestion"):
                    g = a["suggestion"]
                    print(f"  AI suggestion: start {g['start']} · reduce_to {g['reduce_to']} · min_sale {g['min_sale']} EUR")
                if a.get("note"):
                    print(f"  {a['note']}")
        elif args.cmd in engine.MANUAL:
            print(engine.manual(settings, store, args.folder, args.cmd))
        elif args.cmd in ("gui", "panel"):
            from .gui import launcher
            store.close()
            gui = args.cmd == "gui" and not args.no_window
            launcher.run(settings, window=gui, background=getattr(args, "background", False), tray=gui)
        elif args.cmd == "install-desktop":
            from .gui import launcher
            print(f"✅ {launcher.install_desktop_entry()}")
    except (engine.PortalError, RuntimeError, ValueError, TimeoutError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
