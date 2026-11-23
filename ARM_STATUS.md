# Arm Status

Final state after the NYC Non-Slop Hackathon on 22 Nov 2026. The default branch holds the code that ran. Card photos, webcam frames and run videos stay in the git-ignored `private/` folder on the arm laptop.

## What ran on stage

A visitor's handwritten card goes on the box. The overhead camera and Reducto read it. Hermes picks an exact phrase of at most 3 words from the reply cards. The SO-101 writes that phrase in ink on the blank card underneath. The AI writes none of the words.

| Time | Job | Inked | Writing time |
|---|---|---|---|
| 15:46 | 706 | Don't Give Up | 51 s |
| 16:12 | 701 | Keep pushing You'll | 64 s |
| ~16:15 | 700 | I hope you | about 41 s (planned) |
| 16:42 | 700 | you are okay | 50 s |
| 18:13 | 702 | proud of you | 49 s |
| 18:15 | 703 | don't worry | 48 s |

"Keep pushing You'll" and "I hope you" were fragments. The phrase fix at 16:23 stopped those, and the three runs after it were complete replies.

The live rail had 5 reply cards and 8 phrases. Card 706 is handwritten. Cards 700-703 are typed human lines from the GoEmotions archive and show an ARCHIVE stamp. Reply cards return to the rail after each run, so two visitors can get the same reply.

## Known limits

- **Speed.** A reply reaches the screen in roughly 15-18 s (estimated from step timings, not measured end to end). Writing then takes about 50 s.
- **Handwriting.** The arm writes in a pen font. `mode: "trace"` is not implemented, so it does not copy the original writer's strokes.
- **Ink.** Some strokes are faint or uneven. The pipeline runs above were not scored for ink.
- **Pen height** is one value for the whole card (first ink 49 mm, writing 46 mm). Moving the box or pen means measuring it again.
- **Pangram** is off in the live gate. It caught 13 of 17 AI-written lines, under the 0.8 bar. See `docs/hermes-results.md`.
- **Hermes and the arm.** On stage the worker drove the arm directly for speed. The bounded Hermes arm role in `sim/arm_mcp.py` ran one supervised write, which narrowly missed its ink check. See `docs/evidence/README.md`.
- **Idle stack.** The lorem ipsum sample cards can still appear there.
- **`arm relax` frees only the shoulder lift and elbow.** To make the whole arm limp, disable torque on every motor and read `Torque_Enable` back.

## Quote rules

- A phrase is 2 or 3 consecutive words from one written row, at most 40 characters. A short card that fits the limit can be offered whole.
- Hermes must choose a listed phrase exactly. Code checks it, so Hermes cannot rewrite or invent words.
- A phrase cannot begin right after a negation, so "don't give up" cannot become "give up".
- Fragments that end on a weak word or hang on "you" are not offered.
- The pen drops trailing `!?.,;:` marks.

## Safety

- **Gate Hermes** has no tools. It labels each card ok, self_harm, threat, hate, sexual or private_info, and any failure holds the card for a person.
- Phone numbers, emails, links and handles are held by rule and are not sent to a model.
- A held card stays off the rail and the public screen until the operator releases it.
- **Chooser Hermes** has one tool, `give`.
- The arm writes only after the card it read has left the box and a blank card is still. If that does not happen within the wait, it refuses the job.
- The wrist camera faces the room, so its frames are never sent.

## Run it

Only start the worker with someone at the arm. It moves the pen as soon as a job arrives and the card is clear.

From a fresh clone:

```bash
git submodule update --init
git -C vendor/so101-sketch apply ../../sim/so101_sketch_local.patch
python3 sim/defaults.py apply
```

The arm, camera and server commands below use a `.venv` with LeRobot 0.7.0 and `card/requirements.txt`.

The stage stack, one process per terminal, from the repo root:

```bash
# card server and dashboard (needs REDUCTO_API_KEY)
HMD_DATA=private/live HMD_LAYOUT=card/layout.stage.json \
HMD_GATE_CMD="hermes -p gate chat --query-file - -Q --format stream-json --max-turns 1" \
HMD_HERMES_CMD="$PWD/sim/chooser_local.sh" \
.venv/bin/python -m card serve --camera remote --port 8042

# arm server and cameras
(cd sim && ../.venv/bin/python armd.py)
(cd sim && ../.venv/bin/python -B camd.py)

# dashboard feeds
.venv/bin/python -B sim/pose_send.py --url http://127.0.0.1:8042
.venv/bin/python -B sim/cam_send.py --url http://127.0.0.1:8042

# worker: this one moves the real arm
.venv/bin/python -B sim/arm_worker.py --run --root private/live --wait-clear 90
```

This needs two local Hermes profiles: `gate` (no tools, one turn) and `chooser` (one MCP server, `card.body_server`). `python -m card hermes up` builds both in Docker instead.

Open `http://127.0.0.1:8042/`, or `/op` for the operator page. Put the visitor's card on top of a clean blank card and press **Next card**. When the reply appears, lift the visitor's card and keep hands clear.

## Interfaces

- **Jobs:** the card server writes `jobs/NNN.json`. The worker replies in `done/NNN.json`, with `delivered_at` and `duration_s`, or `refused` and a reason.
- **Pose:** `POST /pose` with `{"pos": {"shoulder_pan": deg, "shoulder_lift": deg, "elbow_flex": deg, "wrist_flex": deg, "wrist_roll": deg}, "phase": "write:031"}`. Another machine must send the token.
