# Arm Status

Updated Sun 22 Nov 2026, 13:27. Arm code is on `alex/arm-pen`. The arm stays on Alex's laptop.

## Working

- `sim/armd.py` holds the arm at 50 Hz. `sim/arm` sends commands in about 40 ms.
- `sim/arm_worker.py` polls jobs, plans the pen path and writes `done/NNN.json`.
- The real arm wrote one font job end to end at 38 mm/s. It was partly legible, with gaps.
- Plan-only loop against `card/server.py` at `02b93fb` passed:
  - jobs and images reached the arm side
  - a withdrawn job was removed
  - a card not OK to show stayed off the arm side
  - replies reached the card server
  - the arm did not move

## Simulation clip

`docs/hand-me-down-sim-demo.mp4` replays the planned motion and is labeled SIMULATION. It does not show servo lag, shake or real ink, so it does not prove the physical arm writes this cleanly.

For the arm-offline switch: `git show origin/alex/arm-pen:docs/hand-me-down-sim-demo.mp4 > screen/sim-demo.mp4`

## Not ready

- Writing is improving but not cleared. With joined strokes, one 18 mm word at 24 mm/s wrote cleanly on the real arm. Longer lines are untested at this size, and the arm is 0 of 3 on real pipeline runs.
- `mode: "trace"` is not implemented on the arm. It falls back to font and writes `line`.
- The arm worker does not send a `refused` reply yet. The card side already accepts one.
- Pen height is one value for the whole card (first ink 49 mm, writing 46 mm). Moving the riser or pen means measuring it again.

## Plan: Hermes drives the arm

Status: planned, not built. Today Hermes picks the card and deterministic code drives the arm.

- **Two Hermes roles.** The chooser stays on the card laptop, reads card text and has only `give`. A separate `arm` profile on Alex's laptop runs the arm. It sees job numbers and measurements, and never receives card text, so a hostile card cannot steer it.
- **Arm tools:** `arm_status`, `write_job(job, press_mm, speed)`, `finish_job(job, note)`, `refuse_job(job, reason)`, `stop_arm`.
- **Limits enforced in code:** queued jobs only, no free text, no raw joint commands, press 2-4 mm, speed 0.7-1.2x, blank-card check before writing, and a return to L after every run.
- **Feedback loop:** after each write, the webcam scores ink along the planned path. Hermes sees recent runs and chooses the next press and speed inside those limits.
- **Build order:** tools in dry mode, one real Hermes turn on a fake job, then one supervised run on the real arm.
- **Card side:** no job contract change. The existing `refused` reply is what `refuse_job` will send. Keep `jobs/` empty until Alex says connect.

## Needed from the card side before production

1. Set `mode` to `font` in `card/layout.json` until the arm can trace.
2. Have Alex and Carson write the real house line by hand. The arm refuses the placeholder.
3. Reject lines over 60 characters before writing a job. In tests, 66-67 characters fit and 74-77 did not; the exact limit depends on the words.
4. Add `REDUCTO_API_KEY` to the card laptop's `.env` and run the read test on real index cards.
5. Expect about 35-125 s per card at the current speed, depending on length.
6. Start `python -m card serve --lan` and send Alex the printed `arm_sync` line.
7. Keep `require_tick` true for visitors. With no tick area set, every card counts as not OK to show and the rail stays empty. Draw a small "OK to show" box at the top right of each blank card, above the red line, then mark it with `python -m card layout`. Starting guess: `[0.86, 0.04, 0.08, 0.13]`.

## Before pairing the laptops

- Remove any job in the card laptop's `jobs/` that has no `done` file. The worker writes every unreplied job when it starts.
- The arm side keeps `private/live/jobs` and `private/live/done` empty before the first sync.
- A committed `tick: null` makes every card private. Mark the tick box before testing the real rail.
- If the arm refuses a job, the operator sees NO REPLY after about 225 s, but the visitor screen stays on "writing". Add a timeout state there.

## Dashboard with the live 3D arm

The public screen should show each card's journey and a live 3D view of the arm.

- Keep `screen/index.html` as the main display and `/op` as the operator page.
- Add a token-protected `POST /pose` and a read route `GET /pose.json`. Body: `{"pos": {"shoulder_pan": deg, "shoulder_lift": deg, "elbow_flex": deg, "wrist_flex": deg, "wrist_roll": deg}, "phase": "write:031", "t": unix_seconds}`.
- Render it in the browser on the card laptop with three.js, using the meshes in `sim/so101/` and the box and card from `sim/defaults.json`. Joint degrees use the model's zero with signs +1.
- If no pose arrives for 2 s, label the panel ARM OFFLINE. The recorded clip must stay labeled SIMULATION.
- The arm side will add the pose sender once this route exists.

## Arm laptop start order

Only start the worker with someone at the arm. It moves the pen as soon as a job arrives.

```bash
# already running
cd ~/dev/hand-me-down/sim && ../.venv/bin/python armd.py

# sync, using the line printed by the card laptop
cd ~/dev/hand-me-down && .venv/bin/python private/live/card/arm_sync.py --host IP --port 8040 --token TOKEN --root private/live

# worker
cd ~/dev/hand-me-down/sim && ../.venv/bin/python -B arm_worker.py --run --root ../private/live
```

`private/` is git-ignored and holds card photos, webcam frames and live job data. Do not commit images from it.
