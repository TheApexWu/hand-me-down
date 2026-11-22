"""The one default setup: joint zero, pen height, card and riser box.

  python defaults.py check   compare the live calibration and arm against it
  python defaults.py apply   restore the live calibration files from it (restart armd afterwards)
"""
import json
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / "state"
D = json.loads((HERE / "defaults.json").read_text())


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def put(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def arm_pos():
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        s.connect(str(STATE / "armd.sock"))
        s.sendall(b'{"c":"state"}\n')
        b = b""
        while not b.endswith(b"\n"):
            b += s.recv(65536)
        s.close()
        return json.loads(b)["pos"]
    except OSError:
        return None


def check():
    pen, ink, off = load(HERE / "pen.json"), load(STATE / "ink_contact.json"), load(STATE / "joint_offsets.json")
    dot = [round(v * 1000, 1) for v in pen.get("dot_base_m", [0, 0])[:2]]
    rows = [("wrist roll offset", D["joint_offsets_deg"]["wrist_roll"], off.get("wrist_roll")),
            ("pen length mm", D["pen"]["length_mm"], pen.get("pen_length_mm")),
            ("card x mm", D["card"]["center_mm"][0], dot[0]), ("card y mm", D["card"]["center_mm"][1], dot[1]),
            ("card rotation deg", D["card"]["rotation_deg"], pen.get("card_rotation_deg")),
            ("first ink mm", D["pen"]["first_ink_z_mm"], ink.get("first_ink_z_mm"))]
    bad = 0
    for name, want, got in rows:
        ok = got is not None and abs(got - want) < 0.051
        bad += not ok
        print(f"{'ok  ' if ok else 'DIFF'} {name}: default {want}, live {got}")
    pos = arm_pos()
    if pos is None:
        print("arm: armd unreachable")
    else:
        worst = max(abs(pos[j] - D["home_deg"][j]) for j in D["home_deg"])
        print(f"arm: {'at home' if worst <= 3 else 'away from home'} (largest joint difference {worst:.1f} deg)")
    return bad


def apply():
    STATE.mkdir(exist_ok=True)
    poses = load(STATE / "poses.json")
    poses["l"] = {"reached_deg": D["home_deg"]}
    put(STATE / "poses.json", poses)
    put(STATE / "joint_offsets.json", D["joint_offsets_deg"])
    ink = load(STATE / "ink_contact.json")
    ink.update(first_ink_z_mm=D["pen"]["first_ink_z_mm"], best_line_z_mm=D["pen"]["write_z_mm"], press_mm=D["pen"]["press_mm"])
    put(STATE / "ink_contact.json", ink)
    pen = load(HERE / "pen.json")
    pen.update(pen_length_mm=D["pen"]["length_mm"], card_rotation_deg=D["card"]["rotation_deg"],
               dot_base_m=[D["card"]["center_mm"][0] / 1000, D["card"]["center_mm"][1] / 1000, D["pen"]["write_z_mm"] / 1000])
    put(HERE / "pen.json", pen)
    print("restored; restart armd to load the joint offset")


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        apply()
    else:
        sys.exit(1 if check() else 0)
