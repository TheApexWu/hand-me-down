"""Write screen/arm/scene.json: the SO-101 as a body tree the browser can draw (screen/arm/arm.js).

  python card/arm_scene.py SIM_DIR [OUT.json]

SIM_DIR is the arm side's sim folder: so101/scene.xml (or so101.xml) and defaults.json.
Everything in the output is metres, base frame, z up. Quaternions are w x y z, as MuJoCo
writes them (three.js wants x y z w). Angles are degrees.
"""
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "screen" / "arm" / "scene.json"
SHOWN_GROUPS = {0, 1, 2}                # MuJoCo draws these by default; 3 and up are collision shapes
# The pen is measured in the rig's gripper_frame_link (so101_new_calib.urdf, vendor/so101-sketch
# kinematics.py), not the MJCF's gripperframe site. Given here in the gripper body's frame.
RIG_FRAME_POS = (-0.0079, -0.000218121, -0.0981274)
PEN_AXIS = (0.0, 0.0, -1.0)             # the frame's +z (sim/pen.json tip_local_m), along the jaws
JAW_HOLD_DEG = -7.4                     # sim/demo_twin.py GRIP_RAD: the moving jaw closed on a pen
RED_LINE_M = 0.024                      # sim/arm_worker.py RED_LINE_V: from the card's centre, away from the arm


def load(path):
    """Parse an MJCF file with every <include> replaced by the included file's contents."""
    root = ET.parse(path).getroot()

    def expand(el):
        out = []
        for child in list(el):
            if child.tag == "include":
                out.extend(load(path.parent / child.get("file")))
            else:
                expand(child)
                out.append(child)
        el[:] = out

    expand(root)
    return root


def default_classes(root):
    """class name -> {tag: attributes}, each class already laid over its parent."""
    classes = {}

    def walk(el, inherited, name):
        own = {tag: dict(a) for tag, a in inherited.items()}
        for child in el:
            if child.tag != "default":
                own.setdefault(child.tag, {}).update(child.attrib)
        classes[name] = own
        for child in el.findall("default"):
            walk(child, own, child.get("class"))

    for top in root.findall("default"):
        walk(top, classes.get("main", {}), top.get("class", "main"))
    return classes


def vec(text, n=None):
    v = [float(x) for x in text.split()]
    if n and len(v) != n:
        sys.exit(f"expected {n} numbers, got '{text}'")
    return v


def qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw]


def rnd(v, places=7):
    return [round(float(x), places) + 0.0 for x in v]


class Model:
    def __init__(self, sim):
        folder = Path(sim) / "so101"
        self.file = folder / "scene.xml" if (folder / "scene.xml").exists() else folder / "so101.xml"
        self.root = load(self.file)
        comp = {k: v for c in self.root.findall("compiler") for k, v in c.attrib.items()}
        self.radians = comp.get("angle", "degree") == "radian"
        if comp.get("eulerseq", "xyz") != "xyz":
            sys.exit("only eulerseq xyz is handled")
        self.meshdir = folder / comp.get("meshdir", "")
        self.classes = default_classes(self.root)
        mesh_default = self.classes.get("main", {}).get("mesh", {})
        self.meshes, self.materials = {}, {}
        for asset in self.root.findall("asset"):
            for m in asset.findall("mesh"):
                a = {**mesh_default, **m.attrib}
                if "refpos" in a or "refquat" in a:
                    sys.exit(f"mesh {a.get('file')}: refpos/refquat are not handled")
                self.meshes[a.get("name") or Path(a["file"]).stem] = (a["file"], vec(a.get("scale", "1 1 1"), 3))
            for m in asset.findall("material"):
                self.materials[m.get("name")] = vec(m.get("rgba", "1 1 1 1"), 4)

    def attrs(self, el, childclass):
        return {**self.classes.get(el.get("class") or childclass or "main", {}).get(el.tag, {}), **el.attrib}

    def quat(self, a, what):
        """Orientation as a unit w x y z quaternion. MJCF quats need not be unit length."""
        for k in ("axisangle", "xyaxes", "zaxis", "fromto"):
            if k in a:
                sys.exit(f"{what}: '{k}' is not handled")
        if "euler" in a:                # x, then the new y, then the new z
            e = vec(a["euler"], 3) if self.radians else [math.radians(x) for x in vec(a["euler"], 3)]
            q = [1.0, 0.0, 0.0, 0.0]
            for i, ang in enumerate(e):
                step = [math.cos(ang / 2), 0.0, 0.0, 0.0]
                step[i + 1] = math.sin(ang / 2)
                q = qmul(q, step)
        else:
            q = vec(a.get("quat", "1 0 0 0"), 4)
        q = np.array(q)
        return rnd(q / np.linalg.norm(q))

    def geom(self, el, childclass):
        a = self.attrs(el, childclass)
        kind = a.get("type", "sphere")
        if int(a.get("group", 0)) not in SHOWN_GROUPS or kind == "plane":
            return None
        what = f"geom {a.get('name') or a.get('mesh') or kind}"
        rgba = vec(a["rgba"], 4) if "rgba" in a else self.materials.get(a.get("material"), [0.5, 0.5, 0.5, 1.0])
        g = {"pos": rnd(vec(a.get("pos", "0 0 0"), 3)), "quat": self.quat(a, what), "rgba": rnd(rgba, 3)}
        if kind == "mesh":
            file, scale = self.meshes[a["mesh"]]
            if not (self.meshdir / file).exists():
                sys.exit(f"{what}: {self.meshdir / file} is missing")
            return {"mesh": file, "scale": rnd(scale), **g}
        if kind not in ("box", "cylinder", "sphere"):
            sys.exit(f"{what}: shape '{kind}' is not handled")
        # MuJoCo sizes: box half-extents; cylinder radius, half-length (along z); sphere radius
        return {"shape": kind, "size": rnd(vec(a["size"])), **g}

    def joint(self, el, childclass, body):
        a = self.attrs(el, childclass)
        what = f"joint {a.get('name')}"
        if a.get("type", "hinge") != "hinge" or float(a.get("ref", 0)) != 0:
            sys.exit(f"{what}: only hinges with ref 0 are handled")
        axis = np.array(vec(a.get("axis", "0 0 1"), 3))
        j = {"name": a["name"], "body": body, "axis": rnd(axis / np.linalg.norm(axis)),
             "pos": rnd(vec(a.get("pos", "0 0 0"), 3))}
        if "range" in a:
            lo, hi = vec(a["range"], 2)
            j["range_deg"] = rnd([math.degrees(lo), math.degrees(hi)] if self.radians else [lo, hi], 3)
        return j

    def body(self, el, childclass):
        childclass = el.get("childclass") or childclass
        if el.find("freejoint") is not None or len(el.findall("joint")) > 1:
            sys.exit(f"body {el.get('name')}: one hinge per body is all that is handled")
        joint = el.find("joint")
        geoms = [g for g in (self.geom(e, childclass) for e in el.findall("geom")) if g]
        return {"name": el.get("name"), "pos": rnd(vec(el.get("pos", "0 0 0"), 3)),
                "quat": self.quat(el.attrib, f"body {el.get('name')}"),
                "joint": self.joint(joint, childclass, el.get("name")) if joint is not None else None,
                "geoms": geoms, "children": [self.body(b, childclass) for b in el.findall("body")]}

    def tree(self):
        worlds = self.root.findall("worldbody")
        loose = [g for w in worlds for g in (self.geom(e, None) for e in w.findall("geom")) if g]
        return {"name": "world", "pos": [0.0, 0.0, 0.0], "quat": [1.0, 0.0, 0.0, 0.0], "joint": None,
                "geoms": loose, "children": [self.body(b, None) for w in worlds for b in w.findall("body")]}


def walk(body):
    yield body
    for child in body["children"]:
        yield from walk(child)


def table(d):
    """The riser box and the card on it, placed as sim/penlib.py add_rig places them."""
    box, card = d["box"], d["card"]
    top = box["top_z_mm"] / 1000

    def yaw(deg):
        return rnd([math.cos(math.radians(deg) / 2), 0, 0, math.sin(math.radians(deg) / 2)])

    size = [v / 1000 for v in box["size_mm"]]
    return ({"name": box["name"], "size": rnd(size), "yaw_deg": box["yaw_deg"], "quat": yaw(box["yaw_deg"]),
             "pos": rnd([box["center_mm"][0] / 1000, box["center_mm"][1] / 1000, top - size[2] / 2])},
            {"size": rnd([v / 1000 for v in card["size_mm"]]), "yaw_deg": card["rotation_deg"],
             "quat": yaw(card["rotation_deg"]), "red_line_x": RED_LINE_M,
             "pos": rnd([card["center_mm"][0] / 1000, card["center_mm"][1] / 1000, top])})


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    sim = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) == 3 else OUT
    if not (sim / "defaults.json").exists():
        sys.exit(f"{sim / 'defaults.json'} is missing: it lives on the arm branch (origin/alex/arm-pen)")
    model = Model(sim)
    d = json.loads((sim / "defaults.json").read_text())
    tree = model.tree()
    joints = [b["joint"] for b in walk(tree) if b["joint"]]
    box, card = table(d)
    pen_len = d["pen"]["length_mm"] / 1000
    scene = {
        "source": {"model": str(model.file.relative_to(sim)), "defaults": f"{d['name']}, {d['updated']}"},
        "units": {"length": "m", "quat": "w x y z", "angle": "deg", "up": "z"},
        "joints": joints,               # the same objects as in the tree, listed for convenience
        "home_deg": d["home_deg"],
        "hold_deg": {"gripper": JAW_HOLD_DEG},
        "pen": {"body": "gripper", "grip": rnd(RIG_FRAME_POS), "axis": rnd(PEN_AXIS), "length": round(pen_len, 7),
                "tip": rnd(np.array(RIG_FRAME_POS) + pen_len * np.array(PEN_AXIS))},
        "box": box,
        "card": card,
        "world": tree,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(scene, indent=1)
    text = re.sub(r"\[\s+([^\[\]{}]*?)\s+\]", lambda m: "[" + " ".join(m.group(1).split()) + "]", text)   # one line per vector
    out.write_text(text + "\n")
    files = sorted({g["mesh"] for b in walk(tree) for g in b["geoms"] if "mesh" in g})
    shown = sum(len(b["geoms"]) for b in walk(tree))
    print(f"wrote {out}: {sum(1 for _ in walk(tree)) - 1} bodies, {len(joints)} hinges "
          f"({', '.join(j['name'] for j in joints)}), {shown} drawn shapes from {len(files)} mesh files")
    print("meshes: " + " ".join(files))


if __name__ == "__main__":
    main()
