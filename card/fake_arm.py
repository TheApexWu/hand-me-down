#!/usr/bin/env python3
"""A stand-in for the arm, to prove the loop without it. It follows the job contract exactly:

  - polls jobs/[0-9]*.json and skips any job that already has done/NNN.json
  - re-checks that the job still exists right before "moving"
  - writes done/NNN.json atomically: {"delivered_at": "HH:MM", "duration_s": 134}

It moves nothing and writes nothing on paper. Standard library only.

  python card/fake_arm.py --seconds 8          # run beside `python -m card serve`
"""
import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_once(root, seconds, log=print, sleep=time.sleep):
    """Do every job that is waiting. Returns the job numbers delivered."""
    root, delivered = Path(root), []
    (root / "done").mkdir(parents=True, exist_ok=True)
    for job_file in sorted((root / "jobs").glob("[0-9]*.json")):
        done_file = root / "done" / job_file.name
        if done_file.exists():
            continue
        try:
            job = json.loads(job_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue  # half-written or just withdrawn: look again next pass
        if not job_file.exists():  # the contract's re-check, right before moving
            log(f"job {job_file.stem}: withdrawn before the pen moved; skipped")
            continue
        started = time.time()
        log(f"job {job_file.stem}: pretending to write {job.get('line')!r} in {job.get('mode')} mode ({seconds:g} s)")
        sleep(seconds)
        tmp = done_file.with_name(f".{done_file.name}.tmp")
        tmp.write_text(json.dumps({"delivered_at": time.strftime("%H:%M"), "duration_s": round(time.time() - started)}))
        tmp.replace(done_file)
        delivered.append(job_file.stem)
        log(f"job {job_file.stem}: done")
    return delivered


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT), help="where jobs/ and done/ live (default: this repo)")
    ap.add_argument("--seconds", type=float, default=8, help="how long a job pretends to take")
    ap.add_argument("--once", action="store_true", help="do what is waiting, then exit")
    args = ap.parse_args()
    print(f"FAKE ARM watching {args.root}/jobs (nothing moves, nothing is written on paper)")
    while True:
        run_once(args.root, args.seconds)
        if args.once:
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
