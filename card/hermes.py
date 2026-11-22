"""Hermes Agent in Docker, as the chooser.

In this image `hermes chat` is a client of a Hermes gateway daemon, and a cold gateway
takes far longer to start than a visitor will wait. So one container runs the gateway
all day (the image's documented `gateway run` mode) and each turn attaches to it:

  /opt/data  <- the Docker volume hmd-hermes-home: a Hermes home with nothing in it but the
                "chooser" profile, which has no bundled skills and one tool. It has to be a
                volume, not a folder on the Mac: Hermes refuses its own control socket when the
                mount can't hold file modes ("gateway.sock has mode 0755, expected 0600").
  /work/card        <- the repo's card/ folder, read-only: the body server (give) and the quote hook
  /work/cache/turn  <- the one folder it can write: the turn in play and the pick, nothing else
                       (not the repo root: the container must never see .env, cards/ or jobs/)

The model is reached through OpenRouter. The key is OPENROUTER_API_KEY from the environment
(or the git-ignored .env). `up` copies it into the chooser profile's own .env inside the Docker
volume, the place Hermes reads a profile's secrets from; it never goes into the repo, and the
default profile (which has every tool) never gets it. Change the key and run `up` again.

  python -m card hermes up      start the gateway container, create the profile, write its config
  python -m card hermes check   is give the only tool, is the hook wired
  python -m card hermes down    stop and remove the container; add --purge to also delete the
                                volume, which holds the copy of the key
"""
import hashlib
import os
import subprocess
import time

from . import store

IMAGE = os.environ.get("HMD_HERMES_IMAGE", "nousresearch/hermes-agent:v0.23.3")  # pinned: no updates today
MODEL = os.environ.get("HMD_HERMES_MODEL", "deepseek/deepseek-v4.1-flash-1017")
NAME = os.environ.get("HMD_HERMES_CONTAINER", "hmd-hermes")
PROFILE = "chooser"
VOLUME = os.environ.get("HMD_HERMES_VOLUME", "hmd-hermes-home")
PROFILE_DIR = f"/opt/data/profiles/{PROFILE}"
WORK = "/work"  # where the container sees the two folders it is given
MOUNTS = "2"    # bump when the mounts in _start change, so `up` replaces an older container
PY = "/opt/hermes/.venv/bin/python"  # has the MCP SDK the body server needs

# Keys checked against the config template and source inside the v0.23.3 image.
CONFIG = f"""# Written by `python -m card hermes up`. Edit card/hermes.py, not this file.
_config_version: 54
model:
  default: "{{model}}"
  provider: "openrouter"
  base_url: "https://openrouter.ai/api/v1"
agent:
  max_turns: 4
  reasoning_effort: "low"
# the chooser's whole toolbox: the body server's one tool
platform_toolsets:
  cli: [mcp-body]
# no tool_search / tool_describe helpers either: give is the only thing the model can call
tools:
  tool_search:
    enabled: "off"
mcp_servers:
  body:
    command: "{PY}"
    args: ["-m", "card.body_server"]
    env:
      PYTHONPATH: "{WORK}"
      HMD_DATA: "{WORK}"
    timeout: 20
    connect_timeout: 20
    tools:
      include: [give]
      resources: false
      prompts: false
# a quote that isn't in Reducto's read of that card never reaches the tool
hooks:
  pre_tool_call:
    - matcher: "mcp_+body_+give"
      command: "{PY} {WORK}/card/hooks/quote_on_card.py"
      timeout: 10
      fail_closed: true
hooks_auto_accept: true
memory:
  memory_enabled: false
  user_profile_enabled: false
skills:
  creation_nudge_interval: 0
"""

TURN = ["chat", "--query-file", "-", "-Q", "--format", "stream-json", "--max-turns", "4",
        "-t", "mcp-body", "--accept-hooks"]


def docker(*args, timeout=120, **kw):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, **kw)


def inside(*argv, timeout=120, **kw):
    """Run a command in the container as the hermes user (the user the gateway runs as)."""
    return docker("exec", "--user", "hermes", *kw.pop("flags", ()), NAME, *argv, timeout=timeout, **kw)


def hermes(*args, timeout=120):
    """Run one hermes command in the chooser profile; returns CompletedProcess."""
    return docker("exec", NAME, "hermes", "-p", PROFILE, *args, timeout=timeout)


def running():
    return docker("ps", "--filter", f"name=^{NAME}$", "--format", "{{.Status}}").stdout.strip().startswith("Up")


def turn_argv():
    """argv for one chooser turn; the prompt goes in on stdin (nothing is shell-interpreted)."""
    return ["docker", "exec", "-i", NAME, "hermes", "-p", PROFILE, *TURN]


def stop_turns():
    """A turn that ran past its deadline keeps running inside the container: end its client."""
    docker("exec", NAME, "pkill", "-f", "chat --query-file", timeout=10)


def down(purge=False):
    docker("rm", "-f", NAME)
    if purge:
        docker("volume", "rm", VOLUME)


def _key_mark():
    """A short fingerprint of the key in use, so `up` can tell when it has changed."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return hashlib.sha256(key.encode()).hexdigest()[:12] if key else ""


def _code_mark():
    """A fingerprint of the code the gateway keeps loaded (the body server and what it imports)."""
    h = hashlib.sha256()
    for name in ("body_server.py", "quote.py", "store.py", "hooks/quote_on_card.py"):
        h.update((store.ROOT / "card" / name).read_bytes())
    return h.hexdigest()[:12]


def _give_profile_the_key(mark):
    """Put the key in the profile's .env. It travels on stdin, never on a command line."""
    script = (f"cd {PROFILE_DIR} && umask 077 && "
              "{ grep -v '^OPENROUTER_API_KEY=' .env 2>/dev/null; cat; } > .env.hmd && mv .env.hmd .env && "
              f"printf %s {mark} > .hmd-key")
    put = inside("sh", "-c", script, input=f"OPENROUTER_API_KEY={os.environ['OPENROUTER_API_KEY']}\n", flags=("-i",))
    if put.returncode:
        raise RuntimeError("could not write the key into the profile: " + put.stderr[-300:])


# Asks the gateway itself, the way the chat client does, whether it serves the chooser profile.
_SERVED = (
    "import sys; from pathlib import Path; "
    "from hermes_cli.gateway_runtime_discovery import query_identify; "
    "p = query_identify(Path('/opt/data'), timeout=5); "
    "homes = [str(x.get('home') if isinstance(x, dict) else x) for x in (p.get('served_profiles') or [])]; "
    f"sys.exit(0 if p.get('state') == 'ready' and '{PROFILE_DIR}' in homes else 3)"
)


def gateway_ready():
    return inside(PY, "-c", _SERVED, timeout=30, flags=("-w", "/opt/hermes")).returncode == 0


def _start(log):
    docker("rm", "-f", NAME)
    run = docker("run", "-d", "--name", NAME, "--restart", "unless-stopped", "--label", f"hmd.mounts={MOUNTS}",
                 "-v", f"{VOLUME}:/opt/data",
                 "-v", f"{store.ROOT / 'card'}:{WORK}/card:ro",
                 "-v", f"{store.folder('cache/turn')}:{WORK}/cache/turn",
                 "-e", f"HMD_DATA={WORK}",
                 IMAGE, "gateway", "run")
    if run.returncode:
        raise RuntimeError((run.stderr.strip() or "docker run failed")[-400:])
    log(f"started {NAME} from {IMAGE} (gateway run)")
    for _ in range(90):  # the first boot fills the volume; wait until the CLI answers
        if docker("exec", NAME, "hermes", "--version", timeout=60).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"hermes never answered inside {NAME}: docker logs {NAME}")


def up(log=print, wait=240):
    """Start the gateway container, create the profile (if needed), write its config and key, wait for it."""
    mark = _key_mark()
    if not mark:
        raise RuntimeError("OPENROUTER_API_KEY is not set (export it, or put it in .env)")
    mounts = docker("inspect", "-f", '{{ index .Config.Labels "hmd.mounts" }}', NAME).stdout.strip()
    if not running() or mounts != MOUNTS:
        _start(log)
    restart = False
    if inside("test", "-d", PROFILE_DIR).returncode:
        made = docker("exec", NAME, "hermes", "profile", "create", PROFILE, "--no-skills", "--no-alias", timeout=300)
        if inside("test", "-d", PROFILE_DIR).returncode:
            raise RuntimeError("profile create failed: " + (made.stdout + made.stderr)[-400:])
        log(f"created profile {PROFILE} (no bundled skills)")
        restart = True
    text = CONFIG.format(model=MODEL)
    if inside("cat", f"{PROFILE_DIR}/config.yaml").stdout != text:
        put = inside("sh", "-c", f"cat > {PROFILE_DIR}/config.yaml", input=text, flags=("-i",))
        if put.returncode:
            raise RuntimeError("could not write the profile config: " + put.stderr[-300:])
        log(f"wrote the {PROFILE} profile's config.yaml (model {MODEL})")
        restart = True
    if inside("cat", f"{PROFILE_DIR}/.hmd-key").stdout.strip() != mark:
        _give_profile_the_key(mark)
        log(f"gave the {PROFILE} profile the OpenRouter key")
        restart = True
    if inside("cat", f"{PROFILE_DIR}/.hmd-code").stdout.strip() != _code_mark():
        inside("sh", "-c", f"printf %s {_code_mark()} > {PROFILE_DIR}/.hmd-code")
        log("the body server's code changed since the gateway loaded it")
        restart = True
    if restart:  # the gateway reads profiles, their config and their secrets, and loads the body server, when it starts
        docker("restart", NAME, timeout=180)
        log("restarted the gateway to load the profile")
    started = time.time()
    while time.time() - started < wait:
        try:
            if running() and gateway_ready():
                log(f"gateway is serving {PROFILE} ({time.time() - started:.0f} s)")
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise RuntimeError(f"the gateway was not serving {PROFILE} after {wait} s: docker logs {NAME}")


def check(log=print):
    """What the chooser can see and do, straight from Hermes. Returns True if all good."""
    if not running():
        log(f"FAIL {NAME} is not running: python -m card hermes up")
        return False
    good = True

    def report(ok, what, detail=""):
        nonlocal good
        good &= bool(ok)
        log(("ok   " if ok else "FAIL ") + what)
        if not ok and detail:
            log(detail[-600:])

    report(gateway_ready(), f"gateway is serving the {PROFILE} profile")
    report(docker("exec", NAME, "test", "-r", f"{WORK}/.env").returncode != 0, "the container cannot read the repo's .env")
    seen = sorted(docker("exec", NAME, "sh", "-c", f"cd {WORK} && find . -maxdepth 2 -mindepth 1 -not -path './card/*'").stdout.split())
    report(seen == ["./cache", "./cache/turn", "./card"], f"the container sees only card/ and cache/turn/ of the repo (sees: {seen})")
    report(docker("exec", NAME, "sh", "-c", f"touch {WORK}/card/.probe").returncode != 0, "card/ is read-only inside the container")
    test = hermes("mcp", "test", "body")
    out = test.stdout + test.stderr
    report(test.returncode == 0 and "Tools discovered: 1" in out and "give" in out,
           "body server connects and offers exactly one tool, give", out)
    hooks = hermes("hooks", "list")
    report("quote_on_card.py" in hooks.stdout + hooks.stderr, "quote hook registered on pre_tool_call",
           hooks.stdout + hooks.stderr)
    skills = inside("sh", "-c", f"find {PROFILE_DIR}/skills -name SKILL.md | sed 's|.*/skills/||; s|/SKILL.md||'").stdout.split()
    report(not any("humanizer" in s for s in skills), f"no humanizer skill in the profile (skills present: {skills or 'none'})")
    return good
