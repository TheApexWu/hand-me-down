"""python -m card <command>      (run from the repo root, with .venv active)

  serve [--lan] [--port N] [--no-camera] [--image FILE]   screen, operator page, layout tool
  layout [--image FILE]     open the layout tool: click the card's corners and the line box
  visit FILE [FILE ...]     run whole visits from photos, no confirm step; prints the job and the time
  seed FILE [FILE ...]      put card photos on the rail
  rail                      list the cards
  hermes up|check|down      the Hermes container that does the choosing (needs OPENROUTER_API_KEY)
  warm [N]                  run N throwaway Hermes turns on made-up cards and time them
  live                      checks against the real Hermes container: hostile cards, the made-up quote
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
from . import hermes, pick, read_card, store, visit


def cmd_visit(args):
    """Whole visits from photos. If photo.txt sits next to photo.jpg and holds what the card really
    says, the read is scored against it. Ends with a summary: accuracy, seconds, every failure."""
    import difflib
    import statistics
    from pathlib import Path
    from .quote import words
    took_all, scores, failures = [], [], []
    for f in args.files:
        t = time.time()
        try:
            card, job = visit.run(file=f)
            if args.approve:
                card = visit.approve(card)
        except Exception as exc:
            failures.append(f"{f}: {type(exc).__name__}: {exc}")
            print(f"\n{f}: FAILED {type(exc).__name__}: {exc}")
            continue
        took = time.time() - t
        took_all.append(took)
        if job is None:
            print(f"\n{f}: card {store.nnn(card['card_no'])} read {card.get('line')!r}: NO JOB: {card.get('no_job')}")
            failures.append(f"{f}: no job: {card.get('no_job')}")
            continue
        print(f"\n{f}: card {store.nnn(card['card_no'])} read via {card.get('read_source') or 'nothing'}: "
              f"{card.get('line')!r} {card.get('read_error') or ''}")
        print(f"  ok to show: {card.get('ok_to_show')}  ->  {'on the rail' if card['status'] == 'waiting' else card['status']}"
              + (f" ({card['cannot_write']})" if card.get("cannot_write") else ""))
        truth = Path(f).with_suffix(".txt")
        if truth.exists():
            want, got = words(truth.read_text()), words(card.get("line") or "")
            score = difflib.SequenceMatcher(None, want, got).ratio() if want else 0.0
            scores.append(score)
            print(f"  read accuracy: {score:.0%} of words" + ("" if want == got else f"   expected {truth.read_text().strip()!r}"))
            if want != got:
                failures.append(f"{f}: misread ({score:.0%}): got {card.get('line')!r}")
        print(f"  -> jobs/{store.nnn(job['card_no'])}.json  picked_by={job['picked_by']}  mode={job['mode']}  "
              f"{took:.1f} s {'OK' if took < 15 else 'SLOW (over 15 s)'}")
        print("  " + json.dumps({k: job[k] for k in ("card_no", "line", "crop", "bbox", "quote", "picked_by",
                                                      "written_at", "mode")}, ensure_ascii=False))
        if job.get("why"):
            print("  why:", job["why"])
        if card.get("read_error"):
            failures.append(f"{f}: {card['read_error']}")
        if took >= 15:
            failures.append(f"{f}: took {took:.1f} s")
        if job["picked_by"] != "hermes":
            failures.append(f"{f}: picked by {job['picked_by']} ({job.get('why')})")
    print(f"\n=== {len(args.files)} photo(s)")
    if took_all:
        print(f"seconds per visit: min {min(took_all):.1f}  median {statistics.median(took_all):.1f}  max {max(took_all):.1f}")
    if scores:
        print(f"read accuracy: {sum(s == 1 for s in scores)} of {len(scores)} lines exact, mean {statistics.mean(scores):.0%} of words")
    else:
        print("read accuracy: not scored (put the true line in photo.txt next to each photo)")
    print("failures: " + ("none" if not failures else ""))
    for line in failures:
        print("  -", line)


def cmd_seed(args):
    for f in args.files:
        card, _ = visit.run(file=f, seed=True)
        if args.approve:
            card = visit.approve(card)
        print(store.nnn(card["card_no"]), card["status"], repr(card.get("line")), card.get("read_error") or "")
    if not args.approve:
        print("These reads are unchecked, so the cards are not on the rail yet. Compare each with its card, then "
              "approve them on the operator page, or seed again with --approve if you already have.")


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


def cmd_hermes(args):
    if args.action == "up":
        hermes.up()
        hermes.check()
    elif args.action == "check":
        sys.exit(0 if hermes.check() else 1)
    else:
        hermes.down(purge=args.purge)
        print("removed", hermes.NAME, "and its volume" if args.purge else "(volume kept)")


def cmd_doctor(_):
    ok = lambda good, msg: print(("ok   " if good else "FAIL ") + msg)
    ok(bool(os.environ.get("REDUCTO_API_KEY")), "REDUCTO_API_KEY in the environment (or in .env, which is git-ignored)")
    ok(read_card.online(), "network: platform.reducto.ai reachable")
    ok(read_card.online(pick.MODEL_HOST), f"network: {pick.MODEL_HOST} reachable")
    from .capture import find_camera, list_cameras
    try:
        ok(True, f"camera: {find_camera()[1]}")
    except RuntimeError as exc:
        ok(False, str(exc))
    print("     cameras seen:", ", ".join(f"[{i}] {n}" for i, n in list_cameras()) or "none")
    layout = lay.load()
    ok(lay.measured(layout), "card/layout.json measured (python -m card layout)")
    from . import jobs
    ok(not jobs.cannot_write(layout["house_line"], layout),
       'house line written by the team ("house_line" in card/layout.json)'
       + (f": {jobs.cannot_write(layout['house_line'], layout)}" if jobs.cannot_write(layout["house_line"], layout) else ""))
    ok(bool(layout.get("tick")) or not layout["require_tick"],
       'tick box area set in the layout (without it every card counts as not OK to show)')
    ok(bool(os.environ.get("OPENROUTER_API_KEY")), "OPENROUTER_API_KEY in the environment")
    if pick.HERMES_CMD:
        ok(bool(shutil.which(pick.HERMES_CMD.split()[0])), f"chooser command (HMD_HERMES_CMD): {pick.HERMES_CMD}")
    else:
        ok(hermes.running(), f"Hermes container {hermes.NAME} running ({hermes.IMAGE}, model {hermes.MODEL})")
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
    for cmd in (v, sd):
        cmd.add_argument("--approve", action="store_true",
                         help="you have compared each read with its card and it is right: put the cards on the rail")
    sub.add_parser("rail").set_defaults(fn=cmd_rail)
    w = sub.add_parser("warm"); w.add_argument("n", type=int, nargs="?", default=1); w.set_defaults(fn=cmd_warm)
    h = sub.add_parser("hermes"); h.add_argument("action", choices=["up", "check", "down"]); h.add_argument("--purge", action="store_true"); h.set_defaults(fn=cmd_hermes)
    sub.add_parser("live").set_defaults(fn=lambda _: sys.exit(0 if __import__("card.live_checks", fromlist=["x"]).run_all() else 1))
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
