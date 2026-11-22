"""Let Hermes run the arm for one pass over the queue, through the bounded tools in arm_mcp.py.

  python hermes_drive.py setup [--root DIR]   create or update the isolated "arm" Hermes profile
  python hermes_drive.py run                  one Hermes turn; dry unless state/hermes_arm_live exists
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HERMES = str(Path.home() / ".local" / "bin" / "hermes")
PROFILE = "arm"
PROFILE_DIR = Path.home() / ".hermes" / "profiles" / PROFILE
TOOLS = ["arm_status", "write_job", "finish_job", "refuse_job", "stop_arm"]

CONFIG = """_config_version: 54
model:
  default: "anthropic/claude-haiku-5.5"
  provider: "nous"
agent:
  max_turns: 14
  reasoning_effort: "low"
platform_toolsets:
  cli: [mcp-hand]
tools:
  tool_search:
    enabled: "off"
mcp_servers:
  hand:
    command: "{python}"
    args: ["-B", "{server}"]
    env:
      HMD_ROOT: "{root}"
    timeout: 240
    connect_timeout: 40
    tools:
      include: {tools}
      resources: false
      prompts: false
memory:
  memory_enabled: false
  user_profile_enabled: false
skills:
  creation_nudge_interval: 0
"""

PROMPT = """You operate one pen-writing robot arm through your tools. Clear the job queue once.

1. Call arm_status. If the arm is moving or unavailable, stop and say why.
2. For each queued job, oldest first, call write_job. Start from press_mm 3.0 and speed 1.0. If recent_runs
   shows low ink_coverage, raise press_mm by 0.5 or lower speed by 0.1. Change only one setting per job.
3. If write_job says live is false, call finish_job.
4. If it was live, call finish_job only when ink_coverage is at least 0.50 and stray_ink is at most 0.30.
   Otherwise leave the job open, stop, and say which one setting you would change. A person must load a new card.
5. If write_job says the job cannot be written, call refuse_job with that short reason and continue.
6. If the arm reports a stop or fault, call stop_arm and end.

You cannot see what a job says. End with one plain line: jobs finished, refused and left open."""


def setup(root):
    if not PROFILE_DIR.exists():
        made = subprocess.run([HERMES, "profile", "create", PROFILE, "--no-skills", "--no-alias",
                               "--description", "Bounded tools for one pen arm"], capture_output=True, text=True, timeout=120)
        if not PROFILE_DIR.exists():
            sys.exit("profile create failed: " + (made.stdout + made.stderr)[-500:])
    (PROFILE_DIR / "config.yaml").write_text(CONFIG.format(python=ROOT / ".venv/bin/python", server=HERE / "arm_mcp.py",
                                                           root=root, tools=json.dumps(TOOLS)))
    subprocess.run([HERMES, "gateway", "stop"], capture_output=True, text=True, timeout=30)   # it caches profile config
    clear_servers()
    print("profile ready:", PROFILE, "| job root:", root)


def clear_servers():
    """A tool server left by an earlier turn keeps its old job folder, so Hermes would see the wrong queue."""
    exact = f"{ROOT / '.venv/bin/python'} -B {HERE / 'arm_mcp.py'}"
    for row in subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout.splitlines():
        pid, _, command = row.strip().partition(" ")
        if command.strip() == exact:
            os.kill(int(pid), signal.SIGTERM)
            print("stopped stale arm tool server", pid)


def run():
    log = ROOT / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-hermes-arm.jsonl"
    clear_servers()
    time.sleep(0.3)
    try:
        with open(log, "wb") as out:
            p = subprocess.run([HERMES, "-p", PROFILE, "chat", "--query-file", "-", "-Q", "--format", "stream-json",
                                "--max-turns", "14", "-t", "mcp-hand"], input=PROMPT.encode(), stdout=out,
                               stderr=subprocess.STDOUT, timeout=420)
    finally:
        clear_servers()
    print("hermes rc", p.returncode, "| transcript", log)
    return p.returncode


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "run"])
    ap.add_argument("--root", default=str(ROOT / "private" / "live"))
    a = ap.parse_args()
    if a.cmd == "setup":
        setup(a.root)
    else:
        sys.exit(run())
