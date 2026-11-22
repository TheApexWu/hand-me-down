# Hermes results and observations (card side, 22 Nov 2026)

Internal engineering notes from `carson/card-side`. Raw data sits next to this file:

- `results/hermes-runs-2026-11-22.json`: every gate check and visit from today's two scratch runs
- `results/pangram-bank-2026-11-22.json`: Pangram's score for each of the 51 bank rows (the texts are in `card/message_bank.jsonl`, matched by `id`)
- `results/archive-screen-2026-11-22.jpg`: the visitor screen during an archive pick (choosing, writing, delivered)

The runs used Alex's five lorem-ipsum sample cards and the public bank lines only. No photos are in the repo, and neither is the threat test line.

## Setup

- `nousresearch/hermes-agent:v0.23.3` runs in Docker as `hmd-hermes`, in `gateway run` mode. Its home is the Docker volume `hmd-hermes-home`.
- The model is `deepseek/deepseek-v4.1-flash-1017`, reached through OpenRouter.
- `python -m card hermes up` creates and configures two profiles:
  - **chooser**: one tool, `give` (`card/body_server.py`). The quote hook runs on `pre_tool_call` and fails closed. Tool search is off, memory is off, max_turns 4.
  - **gate**: no tools, no skills, memory off, max_turns 1. `card/gate.py` calls it as `docker exec -i hmd-hermes hermes -p gate chat --query-file - -Q --format stream-json --max-turns 1`.

`python -m card hermes check` reports every line as `ok`:

```
ok   gateway is serving the chooser profile
ok   the container cannot read the repo's .env
ok   the container sees only card/ and cache/turn/ of the repo (sees: ['./cache', './cache/turn', './card'])
ok   card/ is read-only inside the container
ok   body server connects and offers exactly one tool, give
ok   quote hook registered on pre_tool_call
ok   no humanizer skill in the profile (skills present: ['autonomous-ai-agents/hermes-agent'])
ok   the gate profile has no skills (0 found)
ok   the gate profile has no tools, no MCP servers, memory off and one turn
ok   hermes lists no enabled tools for the gate (none)
```

## Gate: safety label before a card can reach anyone

| Input | Label | Held | Time |
|---|---|---|---|
| An ordinary sad line (scratch) | ok | no | 4.0 s, including a Pangram call (Pangram is now off) |
| A threat line (scratch, not committed) | threat | yes | 2.1 s |
| The five sample cards, seeded onto the rail | ok ×5 | no | |
| The same five cards captured again as visitors | ok ×5 | no | |

The held-card flow was tested on a scratch server with the real gate profile. All 13 checks passed:

- The threat card was held, and the operator saw "held for a person: threat".
- The visitor screen state stayed idle and carried none of the card's words.
- The card was not in the idle stack, and its ink and line images returned 404 to the visitor screen.
- `/api/status` listed it as held, and /op renders a "Release to rail" button for it.
- A release request from another machine was refused. A release from the operator put the card on the rail.
- The sad card went on to confirm.

A gate turn is about 600 input tokens, with no tool schemas: Hermes lists 0 enabled tools for the gate profile.

## Chooser: quote mode

Five visits with the real Hermes chooser and the fake arm (`gate-loop` in the JSON). The cards were seeded and approved first, as on /op:

| Visit | Stranger card | Quote | Chars | Words | Exactly on the card | Picked by | Pick time |
|---|---|---|---|---|---|---|---|
| 1 | 003 | Pretium ex | 10 | 2 | yes | hermes | 2.6 s |
| 2 | 004 | Ad litora | 9 | 2 | yes | hermes | 2.6 s |
| 3 | 005 | jumped over | 11 | 2 | yes | hermes | 2.6 s |
| 4 | 001 | dolor sit amet | 14 | 3 | yes | hermes | 1.9 s |
| 5 | 002 | Ut ehlm ad | 10 | 3 | yes | hermes | 2.6 s |

All five were delivered by the fake arm, and none was held.

**Archive pick** (`archive-demo`): only two archive lines were waiting. Hermes chose "so sorry!" from bank line h08 ("I am so sorry! I hope you are okay!", GoEmotions) in 3.3 s. The job carried `provenance: archive`, `bank_id: h08` and the source. On the screen:

- The pick showed as a typed slip stamped ARCHIVE, never as a visitor's card.
- The postmark read ARCHIVE.
- The counter kept the two apart ("1 WAITING 1 ARCHIVE").

**Earlier today** (quote-mode handoff): ten visits from the five sample cards, with real Reducto, real Hermes and the fake arm.

- All ten quotes were valid (9–15 chars) and exactly on the stranger's card.
- Nine were Hermes picks. One was a rule pick, caused by a cold first turn, since fixed by the warm-up turn in `hermes up`.

**Live checks** (`HMD_LIVE=1`, `card/live_checks.py`, part of the 109 passing tests):

- Hermes used only `give`.
- A quote that is on the card but is not a listed phrase ("porch light on for") was refused by the hook.

A chooser turn is about 10.3k input tokens (9.6k of them cached) and about 3.5 s.

## Pangram: the AI-writing check

`python -m card.pangram_eval runs/pangram-bank.json` scored 51 of 51 rows:

| Label | Rows | Flagged as AI | Missed or flagged ids |
|---|---|---|---|
| human_public | 17 | 0 (0.00) | none flagged |
| ai_generated | 17 | 13 (0.76) | missed a01, a02, a11, a13 |
| ai_rewritten | 17 | 5 (0.29) | caught r02, r09, r13, r16, r17 |

`ready_to_claim` is **false**: it needs at least 0.8 of AI lines caught and at most 0.1 of human lines flagged.

Decision: Pangram did not work reliably on very short text. It stays out of the pitch and out of the live decision. `PANGRAM_LIVE` is false in `card/gate.py`, so visitors' words are not sent to Pangram at all. `HMD_PANGRAM_LIVE=1` turns it back on, and even then it can only hold a card for a person, never reject one.

## Observations

1. **Restarts.** The gateway has to restart to load a new profile, config or body-server code. `hermes up` restarts it when the body server's code fingerprint changes.
2. **Cold first turn.** The first chooser turn after a restart is cold and can die (exit 143). `hermes up` spends a warm-up turn so a visitor never gets it.
3. **Turn folder.** The chooser's turn folder must be the one the container mounts (the repo's `cache/turn`). A dry run with its own `HMD_DATA` must set `HMD_TURN`. Otherwise Hermes answers against a stale turn ("this turn is over") and the rule picks instead. That is what happened on the first archive run today.
4. **Overdue turns.** `stop_turns()` used to `pkill` every `chat --query-file` in the container, which would also end a gate check in flight. It now ends only chooser turns.
5. **Shared turn folder.** The demo server and the live server still share one turn folder. Don't run the demo autoplay while real visitors are served.
6. **`hermes tools`** needs a TTY, but `hermes -p gate tools list` works non-interactively, and `hermes check` uses it.
7. **pnpm warning.** Every `hermes` command prints "install out of sync (pnpm: not installed or outdated)". It is harmless, and there is no `hermes update` today.
8. **Tool search.** With tool search on, Hermes exposed helper tools beyond `give`. It is off in both profiles.
9. **Contact details.** Emails, phone numbers, URLs and @handles are caught by regex in `card/gate.py` and are never sent to a model.
