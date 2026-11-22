# Real Arm Run Evidence

Photos and measurements from the physical SO-101 on 22 November 2026. The cards use test text only. Images are cropped to the card, and raw webcam frames are not published.

| Image | What changed | Result |
|---|---|---|
| [01](images/01-first-real-write.jpg) | First real font job, 10 mm letters, 38 mm/s | Partly legible, with gaps |
| [02](images/02-pressure-lines.jpg) | Slower pen, plus five test lines at different pressures | Straight lines inked, letters still broke |
| [03](images/03-direction-test.jpg) | Strokes in four directions at four heights | Every direction inked, sideways strokes bowed |
| [04](images/04-speed-test.jpg) | Same word at 0.5x and 0.3x speed | Less measured shake, letters still broke |
| [05](images/05-big-word.jpg) | 18 mm letters at 24 mm/s | First legible word |
| [06](images/06-joined-strokes.jpg) | Pen stays down through each letter | Cleaner word, connected `m` |
| [07](images/07-hermes-live-write.jpg) | Hermes ran the arm through bounded tools | Readable, narrowly missed its ink check, left open |

## Data

- `run_metrics.jsonl`: scored test runs, including speed tests and the live Hermes write.
- `hermes-live-run.json`: the tools Hermes called, their results and its final decision.
- `hermes-live-joints.csv`: commanded and measured joint angles for that run, 1,625 ticks at 50 Hz.

The ink pass line comes from one real card, so treat it as a rough check. The arm has not yet passed 3 clean runs through the full card pipeline.
