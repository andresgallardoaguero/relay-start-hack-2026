# Relay

![The approval inbox, with two purchases that Relay paused for the customer](docs/screen_inbox.png)

Relay is a wallet control layer for AI shopping agents. It sits between the agent and the card. The customer writes one instruction in plain language, such as what the agent may buy, up to which amount and from which shops, and Relay turns it into a policy. Every purchase the agent proposes is then checked by a set of guards in five families, which are spending limits, item and terms, seller, session, and repeats and manipulation. Relay answers approve, decline or step_up, where step_up puts the purchase to the customer before any money moves. Every answer arrives inside the platform's deadline and carries its evidence and a trust score from 0 to 100, so the customer can see why a purchase went through or did not.

Relay was built in 24 hours at START Hack Tour St. Gallen 2026 for the Viseca case "Agent on a Leash".

## Results

The figures come from a replay of the 45 purchases in Viseca's five public scenarios. 28 of the 45 are risky on their own facts, which means they need a refusal or a question.

| Setup | Risky purchases stopped or asked | Risky amount paid unchecked | Ordinary purchases approved untouched |
|---|---|---|---|
| No control | 0 of 28 | CHF 5,899.28 | 17 of 17 |
| Plain spending limit | 6 of 28 | CHF 4,173.28 | 17 of 17 |
| Relay | 28 of 28 | CHF 0.00 | 17 of 17 |

![Risky purchases caught by each setup](outputs/pitch/02_risky_purchases_caught.png)

Relay approves 17 of the 45 purchases, asks the customer about 13 and declines 15, which matches the reference decision on all 45. One decision of the engine takes 1.6 ms at the median and 6.3 ms at the slowest, against a deadline of 8 seconds. The full tables and the other charts are in [outputs/pitch/pitch_figures.md](outputs/pitch/pitch_figures.md), and the figures measured on Viseca's server are in [outputs/pitch/business_and_jury_kpis.md](outputs/pitch/business_and_jury_kpis.md).

## Quick start

You need Python 3.12 or newer and Node 20 or newer. Every command runs from the repository root. On Windows the interpreter is `backend\.venv\Scripts\python` instead of `backend/.venv/bin/python`.

The setup is done once.

```text
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock.txt
(cd frontend && npm install)
cp backend/.env.example backend/.env        # fill in the keys only for live mode or the model
```

Then start the app.

```text
scripts/dev.sh                     # offline mode, keeps the decisions from earlier runs
scripts/dev.sh offline --fresh     # offline mode with an empty log and inbox
scripts/dev.sh stop                # stops the backend and the interface
```

The script stops whatever still runs on the two ports, starts the backend on `http://127.0.0.1:8000` and the interface on `http://localhost:5173`, prints which platform the backend talks to, and writes its logs to `outputs/logs/`. `--fresh` moves the old store to `outputs/state/backup-<time>/` rather than deleting it.

Starting by hand in two terminals does the same thing.

```text
LEASH_MODE=offline backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000 --reload
(cd frontend && VITE_USE_MOCK_API=false npm run dev)
```

`http://localhost:8000/docs` lists every endpoint of the backend. Without `VITE_USE_MOCK_API=false` the interface runs on static prototype data and never calls the backend.

## Offline or live

The backend talks to one of two platforms, chosen when it starts. The banner at the top of the interface shows which one.

| Mode | Where the purchases come from | Needs |
|---|---|---|
| `offline` (blue banner) | A copy of Viseca's platform inside the backend, serving the 45 public purchases | Nothing |
| `live` (red banner) | Viseca's server at `https://leash-api-production.up.railway.app` | `TEAM_API_KEY` in `backend/.env` |

Switching means restarting the backend in the other mode, with `scripts/dev.sh live` or `scripts/dev.sh offline`. The script asks for a typed `live` before it starts live mode, and refuses without the key. By hand, set `LEASH_MODE` in `backend/.env` or in the environment, where the environment wins over the file. `backend/.env.example` says `LEASH_MODE=live`, which is why the script always passes the mode explicitly. Both modes use the same endpoints, the same engine and the same screens, so what works offline works live. A restart in offline mode forgets the confirmed policy, because the platform copy lives inside the backend, so confirm it again before the next run.

`scripts/live_scenario_run.py --scenario SCEN0001` runs one scenario against the local backend from the terminal. It confirms the scenario's instruction, starts the run and answers every question from a fixed table.

## The three screens

The usual order is policy, run, inbox, log.

1. **Wallet policy.** Write the customer's instruction in plain words, then press **Build policy**. The compiler turns it into checks, split into the rules stored with the platform and the checks the engine enforces on top, and lists under "When Relay will ask" anything it read with doubt. Press **Confirm policy** to store it on the platform as the active mandate.
2. **Start a run.** Once a policy is active, pick a scenario under "Demo control" and press **Start offline run**. The backend polls the platform, decides every purchase of the scenario within its 8 second deadline, and the interface jumps to the inbox.
3. **Approval inbox.** Every purchase the engine answered with a question appears as a card with the one sentence the customer reads, the trust score and the reason. It is the image at the top of this page. Press **Approve once** or **Decline** within the 120 second window. An unanswered question expires and counts as not approved. When an approval given meanwhile means this one would break the budget, the card says so, and the customer can still approve it, because the customer always decides.
4. **Decision log.** Every purchase with its decision, its trust score and the five guard families. Expand a row for the evidence of each guard, the deadline margin and the calculation of the score. The method behind the score is described in [docs/trust-score.md](docs/trust-score.md).
5. **Tighten** moves uncertain purchases to decline and **Revoke** withdraws the mandate. Both go to the platform, and neither can loosen anything.

The public scenarios come with a reference instruction each. Use it as the policy for that scenario, because an instruction that does not fit the scenario turns every purchase into a decline and leaves the inbox empty.

| Scenario | Instruction to type | Purchases |
|---|---|---|
| SCEN0000 Connection check | Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. Ask me when uncertain. | 1 |
| SCEN0001 Household budget | Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery, and keep the total across any seven days at or below CHF 300. Ask me when uncertain. | 10 |
| SCEN0002 Requested item and order terms | Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200. Ask me when uncertain. | 12 |
| SCEN0003 Session integrity | The agent may buy clothing for me, up to CHF 250 per order, from shops I have used before. Pause anything that looks like someone other than me is driving the session. Ask me when uncertain. | 11 |
| SCEN0004 Manipulated agent | Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain. | 11 |

The answer the reference expects from the customer for each question is in `data/processed/purchase_verdicts.csv` under `assumed_customer_answer`. A confirmed mandate applies to every run started afterwards. To show another scenario, go back to the policy screen, build and confirm its instruction, then start that scenario.

## Checks

```text
backend/.venv/bin/python -m pytest tests -q                                             # the backend tests
GOAL_FULFILLED_ACTION=note backend/.venv/bin/python scripts/replay.py --all --compare   # all 45 purchases offline, compared with the reference decisions
(cd frontend && npm test && npm run build)                                              # the interface tests and the TypeScript check
```

The tests and the replay need no key, no network and no `.env` file. The replay writes a table and one record per purchase into `outputs/replay/`. With `GOAL_FULFILLED_ACTION=note` it approves 17 purchases, asks about 13 and declines 15, and matches the reference on 45 of 45. The setting decides what happens to a clean second order of the one requested thing. `note` approves it with a note, and `step_up`, the default in `backend/.env.example`, asks the customer first.

## Where things are written

- `outputs/state/relay.sqlite3` is the decision store of the backend, gitignored. `scripts/dev.sh offline --fresh` sets it aside for an empty start.
- `outputs/audit/run_<id>.jsonl` is the append-only audit file of a live run. Offline runs write under `outputs/audit/offline/`, gitignored.
- `outputs/replay/` holds the replay tables, `outputs/trust/` the trust score reports, `outputs/pitch/` the figures and `outputs/logs/` the logs of `scripts/dev.sh`.

## Data

`data/raw/` holds Viseca's synthetic case data, copied unchanged from github.com/START-Hack/viseca-2026. It contains no real customers. `data/processed/` holds the tables derived from it.

## Team

- Vanessa Burckhardt
- Andrés Gallardo
- Jonas Lüthi
- Viktor Vantsev

## Folder map

```text
backend/    The engine, the guards, the policy compiler, the trust score and the web API.
frontend/   The three screens, in React and TypeScript.
tests/      Automated tests of the backend.
scripts/    The replay, the trust report, the pitch figures, the baselines and the start script.
docs/       The trust score method and the image of this page.
data/       The synthetic case data and the tables derived from it.
outputs/    Replay tables in replay/, trust reports in trust/, figures in pitch/.
```
