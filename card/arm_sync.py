#!/usr/bin/env python3
"""Only needed if the arm runs on a different laptop from the card side.

Run on the arm laptop, from the repo root. It keeps the file contract the same on both
machines: pulls new jobs/NNN.json (and the crop and card image each one names) from the card
laptop, removes local jobs the card side has withdrawn, and sends done/NNN.json back when the
arm side writes it. Standard library only.

  python card/arm_sync.py --host 192.168.50.198 --token TOKEN

The card laptop prints this exact line, with its address and token, when it starts with
  python -m card serve --lan
"""
import argparse
import functools
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
print = functools.partial(print, flush=True)  # a person is watching this log, often through a pipe
IMAGE_PATH = re.compile(r"cards/\d{3}[\w.-]*\.(png|jpg)")  # the only paths a job may make us write


def request(base, path, token, data=None, timeout=10):
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sync_once(base, root, token, have, sent, log=print):
    """One pass. Returns how many things could not be done this time (0 = in step).

    Raises only if the card laptop's job list can't be fetched at all. Every job and every
    reply is handled on its own, so one that fails never holds up the others.
    """
    root = Path(root)
    for d in ("jobs", "done", "cards"):
        (root / d).mkdir(parents=True, exist_ok=True)
    problems = 0
    live = set(json.loads(request(base, "/jobs/index.json", token)))

    # 1. Withdrawn jobs go first: the arm re-checks that a job exists right before it moves.
    for local in sorted((root / "jobs").glob("[0-9]*.json")):
        if local.stem not in live:
            local.unlink(missing_ok=True)
            have.pop(local.stem, None)
            log(f"job {local.stem}: withdrawn on the card side, removed here")

    # 2. New or changed jobs. Their images arrive before the job file does.
    for stem in sorted(live):
        try:
            raw = request(base, f"/jobs/{stem}.json", token)
            if have.get(stem) == raw and (root / "jobs" / f"{stem}.json").exists():
                continue
            job = json.loads(raw)
            for key in ("crop", "card_img"):
                if job.get(key):
                    if not IMAGE_PATH.fullmatch(job[key]):
                        raise ValueError(f"refusing odd image path {job[key]!r}")
                    (root / job[key]).write_bytes(request(base, "/" + job[key], token, timeout=30))
            # A reply left over from an earlier job with this number (one that was withdrawn after the
            # arm finished it) would make the arm skip this one: the contract skips any job with a done file.
            stale = root / "done" / f"{stem}.json"
            if stale.exists() and (stale.name, stale.stat().st_mtime_ns) in sent:
                stale.unlink()
                log(f"job {stem}: given again; cleared the old reply so the arm writes it")
            tmp = root / "jobs" / f".{stem}.tmp"
            tmp.write_bytes(raw)
            tmp.replace(root / "jobs" / f"{stem}.json")
            have[stem] = raw
            log(f"job {stem}: {job.get('mode')}  {job.get('picked_by')}  {job.get('line')!r}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue  # withdrawn between the list and the fetch: step 1 clears it next pass
            problems += 1
            log(f"job {stem}: HTTP {exc.code}; will retry")
        except Exception as exc:
            problems += 1
            log(f"job {stem}: {type(exc).__name__}: {exc}; will retry")

    # 3. Replies from the arm.
    for done in sorted((root / "done").glob("[0-9]*.json")):
        key = (done.name, done.stat().st_mtime_ns)
        if key in sent:
            continue
        try:
            request(base, f"/done/{done.name}", token, data=done.read_bytes())
            sent.add(key)
            log(f"done {done.stem}: sent")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # the job is gone on the card side (withdrawn): nothing left to tell it
                sent.add(key)
                log(f"done {done.stem}: that job was withdrawn; settled")
            else:
                problems += 1
                log(f"done {done.stem}: HTTP {exc.code}; will retry")
        except Exception as exc:
            problems += 1
            log(f"done {done.stem}: {type(exc).__name__}: {exc}; will retry")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="the card laptop: IP or name.local")
    ap.add_argument("--port", type=int, default=8040)
    ap.add_argument("--token", default=os.environ.get("HMD_TOKEN"), help="the token the card laptop printed (or HMD_TOKEN)")
    ap.add_argument("--root", default=str(ROOT), help="where jobs/, done/ and cards/ live (default: this repo)")
    ap.add_argument("--once", action="store_true", help="one pass, then exit (0 if in step)")
    args = ap.parse_args()
    if not args.token:
        ap.error("--token is required (the card laptop prints it when it starts with --lan)")
    base = f"http://{args.host}:{args.port}"
    have, sent = {}, set()
    print(f"syncing {args.root}/jobs and done with {base} (Ctrl-C to stop)")
    while True:
        try:
            problems = sync_once(base, args.root, args.token, have, sent)
        except urllib.error.HTTPError as exc:
            problems = 1
            print(f"  card laptop answered HTTP {exc.code}" + (": wrong or missing token" if exc.code == 401 else "") + "; retrying")
        except Exception as exc:
            problems = 1
            print(f"  can't reach the card laptop ({type(exc).__name__}: {exc}); retrying")
        if args.once:
            raise SystemExit(1 if problems else 0)
        time.sleep(1)


if __name__ == "__main__":
    main()
