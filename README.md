# Hand-Me-Down

You handwrite a card. A robot hands you back a few words that another person wrote. The AI writes none of them.

NYC Non-Slop Hackathon, 22 Nov 2026. Alex Wu and Carson Weeks. One SO-101 arm, one ballpoint pen, a box of index cards.

![The arm writing "Don't Give Up" on a 3x5 card](docs/images/arm-wrote-dont-give-up.jpg)

## How it works

Card goes on the box → camera and Reducto read it → a Hermes gate screens it → a second Hermes picks a real phrase from the cards people left (one tool, exact words only, a hook rejects anything changed) → the arm writes it in ink → you keep the card.

## Pictures

| | |
|---|---|
| ![SO-101 with the pen taped into the gripper](docs/images/arm-with-pen.jpg) | ![The rig from above](docs/images/rig-from-above.jpg) |
| ![Handwritten seed card: Don't Give Up](docs/images/seed-card-handwritten.jpg) | ![A vent card written by hand](docs/images/vent-card-handwritten.jpg) |
| ![Arm-written card: you are okay](docs/images/arm-wrote-you-are-okay.jpg) | ![Arm-written card: proud of you](docs/images/arm-wrote-proud-of-you.jpg) |

![The arm on the table at the venue](docs/images/arm-on-the-table.jpg)

Six cards were written on stage: Don't Give Up · Keep pushing · I hope you · you are okay · proud of you · don't worry. About 50 seconds of ink each. More test writes in [docs/evidence](docs/evidence/README.md).

## Built with

Hermes Agent (gate + chooser profiles) · Reducto (handwriting) · Pangram (bank audit) · OpenRouter · SO-101 + LeRobot

## Repo

`card/` card server, Hermes profiles, quote rules · `sim/` arm server, pen path, cameras · `screen/` visitor screen · `docs/` evidence and results · [ARM_STATUS.md](ARM_STATUS.md) to run it

Tests: `python -m pytest card/tests`
