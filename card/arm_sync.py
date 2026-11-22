#!/usr/bin/env python3
"""Only needed if the arm runs on a different laptop from the card side.

Run on the arm laptop, from the repo root. Keeps the file contract the same on both
machines: pulls new jobs/NNN.json (and the crop and card image each one names) from the
card laptop, and sends done/NNN.json back when the arm side writes it. Standard library only.

  python card/arm_sync.py --host 192.168.50.198        # the card laptop, started with: python -m card serve --lan
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="the card laptop: IP or name.local")
    ap.add_argument("--port", type=int, default=8040)
    args = ap.parse_args()
    base = f"http://{args.host}:{args.port}"
    for d in ("jobs", "done", "cards"):
        (ROOT / d).mkdir(exist_ok=True)
    have, sent = {}, set()
    print(f"syncing jobs/ and done/ with {base} (Ctrl-C to stop)")
    while True:
        try:
            for stem in json.loads(get(f"{base}/jobs/index.json")):
                raw = get(f"{base}/jobs/{stem}.json")
                if have.get(stem) == raw:
                    continue
                job = json.loads(raw)
                for key in ("crop", "card_img"):  # fetch the images before the job appears
                    if job.get(key):
                        (ROOT / job[key]).parent.mkdir(parents=True, exist_ok=True)
                        (ROOT / job[key]).write_bytes(get(f"{base}/{job[key]}", timeout=30))
                tmp = ROOT / "jobs" / f".{stem}.tmp"
                tmp.write_bytes(raw)
                tmp.replace(ROOT / "jobs" / f"{stem}.json")
                have[stem] = raw
                print(f"job {stem}: {job['mode']}  {job['picked_by']}  {job['line']!r}")
            for done in (ROOT / "done").glob("[0-9]*.json"):
                key = (done.name, done.stat().st_mtime)
                if key in sent:
                    continue
                req = urllib.request.Request(f"{base}/done/{done.name}", data=done.read_bytes(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).read()
                sent.add(key)
                print(f"done {done.stem} sent")
        except Exception as exc:
            print(f"  ({type(exc).__name__}: {exc}; retrying)")
        time.sleep(1)


if __name__ == "__main__":
    main()
