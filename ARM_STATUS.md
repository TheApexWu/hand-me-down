# Arm Status

Updated Sun 22 Nov 2026, 12:25. Arm code is on `alex/arm-pen`. The arm stays on Alex's laptop.

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

## Not ready

- Writing is not stable yet. The current speed, 16 mm/s, halved shake in an air test but has not been tested in ink.
- `mode: "trace"` is not implemented on the arm. It falls back to font and writes `line`.
- A job the arm refuses gets no `done` file, so the screen would keep waiting.
- Pen height is one value for the whole card (first ink 49 mm, writing 46 mm). Moving the riser or pen means measuring it again.

## Needed from the card side before production

1. Set `mode` to `font` in `card/layout.json` until the arm can trace.
2. Have Alex and Carson write the real house line by hand. The arm refuses the placeholder.
3. Reject lines over 60 characters before writing a job. In tests, 66-67 characters fit and 74-77 did not; the exact limit depends on the words.
4. Add `REDUCTO_API_KEY` to the card laptop's `.env` and run the read test on real index cards.
5. Expect about 85-95 s per two- or three-line card at the current speed.
6. Start `python -m card serve --lan` and send Alex the printed `arm_sync` line.
7. Keep `require_tick` true for visitors. With no tick area set, every card counts as not OK to show and the rail stays empty. Draw a small "OK to show" box at the top right of each blank card, above the red line, then mark it with `python -m card layout`. Starting guess: `[0.86, 0.04, 0.08, 0.13]`.

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
