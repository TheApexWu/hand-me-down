"""Render sign_check.png: what +15 degrees on each arm joint looks like from LeRobot's homing pose.

Each panel blends the homing pose (faint) with the joint at +15 degrees (solid), so on the real arm
you can see whether a +10 degree command moves the same way. Model zero = LeRobot's homing L pose.
"""
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import penlib as pl

HERE = Path(__file__).parent
VIEWS = {  # azimuth, elevation per joint: pick the angle where the motion is obvious
    "shoulder_pan": (180, -70), "shoulder_lift": (90, -10), "elbow_flex": (90, -10),
    "wrist_flex": (90, -10), "wrist_roll": (180, -20),
}


def render(m, d, renderer, q, az, el):
    d.qpos[:] = 0
    d.qpos[:pl.ARM] = q
    mujoco.mj_forward(m, d)
    cam = mujoco.MjvCamera()
    cam.lookat[:] = [0.18, 0.0, 0.15]
    cam.distance, cam.azimuth, cam.elevation = 0.75, az, el
    renderer.update_scene(d, camera=cam)
    return Image.fromarray(renderer.render())


def main():
    m = pl.build_model(offscreen=(640, 480))
    d = mujoco.MjData(m)
    renderer = mujoco.Renderer(m, height=480, width=640)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    panels = []
    home = render(m, d, renderer, np.zeros(pl.ARM), 120, -20)
    ImageDraw.Draw(home).text((12, 10), "homing pose (all joints 0 deg)", fill="white", font=font)
    panels.append(home)
    for j, name in enumerate(pl.ARM_JOINTS):
        az, el = VIEWS[name]
        q = np.zeros(pl.ARM)
        base = render(m, d, renderer, q, az, el)
        q[j] = np.deg2rad(15)
        moved = render(m, d, renderer, q, az, el)
        img = Image.blend(base, moved, 0.65)
        ImageDraw.Draw(img).text((12, 10), f"{name} +15 deg (faint = before)", fill="white", font=font)
        panels.append(img)
    sheet = Image.new("RGB", (640 * 3, 480 * 2), "black")
    for i, p in enumerate(panels):
        sheet.paste(p, ((i % 3) * 640, (i // 3) * 480))
    sheet.save(HERE / "sign_check.png")
    print("saved", HERE / "sign_check.png")


if __name__ == "__main__":
    main()
