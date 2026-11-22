"""python -m card <command>      (run from the repo root, with .venv active)

  serve [--lan] [--port N] [--no-camera] [--image FILE]   screen, operator page, layout tool
  layout [--image FILE]     open the layout tool: click the card's corners and the line box
  visit FILE [FILE ...]     run whole visits from photos, no confirm step; prints the job and the time
  seed FILE [FILE ...]      put card photos on the rail
  rail                      list the cards
  warm [N]                  run N throwaway Hermes turns on made-up cards and time them
  doctor                    check the key, the camera, the layout, Hermes and the body server
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from . import layout as lay
from . import pick, read_card, store, visit


def cmd_visit(args):
    for f in args.files:
        t = time.time()
        card, job = visit.run(file=f)
        took = time.time() - t
        print(f"\n{f}: card {store.nnn(card['card_no'])} read via {card.get('read_source') or 'nothing'}: "
              f"{card.get('line')!r} {card.get('read_error') or ''}")
        print(f"  -> jobs/{store.nnn(job['card_no'])}.json  picked_by={job['picked_by']}  mode={job['mode']}  "
              f"{took:.1f} s {'OK' if took < 15 else 'SLOW (over 15 s)'}")
        print("  " + json.dumps({k: job[k] for k in ("card_no", "line", "crop", "bbox", "quote", "picked_by",
                                                      "written_at", "mode")}, ensure_ascii=False))
        if job.get("why"):
            print("  why:", job["why"])


def cmd_seed(args):
    for f in args.files:
        card, _ = visit.run(file=f, seed=True)
        print(store.nnn(card["card_no"]), card["status"], repr(card.get("line")), card.get("read_error") or "")


def cmd_rail(_):
    for c in store.all_cards():
        print(store.nnn(c["card_no"]), f"{c.get('status'):<8}", c.get("written_at"), repr(c.get("line")),
              c.get("read_error") or "")


def cmd_warm(args):
    for i in range(args.n):
        got = pick.warm()
        print(f"turn {i + 1}: {got['secs']} s  " + (f"gave {got['card']} quoting {got['quote']!r}" if got["card"]
              else f"NO GIFT: {got['why']}") + (f"  ({got['misses']} refused)" if got["misses"] else ""))
    latest = store.read_json(store.folder("cache/trace") / "latest.json") or {}
    print("trace:", latest.get("file"))


def cmd_doctor(_):
    ok = lambda good, msg: print(("ok   " if good else "FAIL ") + msg)
    ok(bool(os.environ.get("REDUCTO_API_KEY")), "REDUCTO_API_KEY in the environment (or in .env, which is git-ignored)")
    ok(read_card.online(), "network: platform.reducto.ai reachable")
    ok(read_card.online(pick.PORTAL_HOST), f"network: {pick.PORTAL_HOST} reachable")
    from .capture import find_camera, list_cameras
    try:
        ok(True, f"camera: {find_camera()[1]}")
    except RuntimeError as exc:
        ok(False, str(exc))
    print("     cameras seen:", ", ".join(f"[{i}] {n}" for i, n in list_cameras()) or "none")
    layout = lay.load()
    ok(lay.measured(layout), "card/layout.json measured (python -m card layout)")
    ok(layout["house_line"] != lay.DEFAULT["house_line"], 'house line written by the team ("house_line" in card/layout.json)')
    exe = pick.HERMES_CMD.split()[0]
    found = shutil.which(exe)
    ok(bool(found), f"chooser command: {pick.HERMES_CMD}")
    if found:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        print("    ", ((v.stdout or v.stderr).strip().splitlines() or ["?"])[0])
    probe = subprocess.run([sys.executable, "-c", "import card.body_server"], capture_output=True, text=True, cwd=store.ROOT)
    ok(probe.returncode == 0, "body server imports" + ("" if probe.returncode == 0 else ": " + probe.stderr[-300:]))
    print(f"     mode: {layout['mode']}   waiting on the rail: {len(store.waiting())}")


def main():
    p = argparse.ArgumentParser(prog="python -m card", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "layout"):
        s = sub.add_parser(name)
        s.add_argument("--lan", action="store_true")
        s.add_argument("--port", type=int, default=8040)
        s.add_argument("--no-camera", action="store_true")
        s.add_argument("--camera")
        s.add_argument("--image", help="use a photo in place of the camera")
    v = sub.add_parser("visit"); v.add_argument("files", nargs="+"); v.set_defaults(fn=cmd_visit)
    sd = sub.add_parser("seed"); sd.add_argument("files", nargs="+"); sd.set_defaults(fn=cmd_seed)
    sub.add_parser("rail").set_defaults(fn=cmd_rail)
    w = sub.add_parser("warm"); w.add_argument("n", type=int, nargs="?", default=1); w.set_defaults(fn=cmd_warm)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    args = p.parse_args()
    if args.cmd in ("serve", "layout"):
        from .server import serve
        serve(port=args.port, lan=args.lan, camera_spec=args.camera, use_camera=not args.no_camera,
              image=args.image, open_page="/layout" if args.cmd == "layout" else None)
    else:
        args.fn(args)


if __name__ == "__main__":
    main()
