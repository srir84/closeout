# Closeout (demo)

Closeout turns finished home-repair jobs into verified records that a homeowner can show when they sell.
Each job can carry up to three marks: **the homeowner confirmed it**, **the contractor confirmed it**, and
**a town permit is on file**. An AI model reads each invoice twice; anything the two reads disagree on goes
to a person, and no email is sent without a person approving those exact words.

This is a demo that runs on your own computer. **Every person, home and company in it is made up.** It is
not tied to any real business. Emails go only to a local test mailbox. Real public data (Manasquan, NJ
permits and parcels) appears only on the data pages, with no people in it.

**Watch the demo:** [the 3-minute walkthrough](https://srir84.github.io/closeout/demo.html)
([video file](docs/demo/closeout-demo.mp4)) · [the clickable mock](https://srir84.github.io/closeout/)

## Quickstart

You need Docker (with Compose) and `make`. Nothing else: no AI model downloads, no accounts, no keys to fetch.

```sh
git clone https://github.com/srir84/closeout && cd closeout
make demo        # builds, starts, plays every role, prints a pass/fail list (about 5-8 minutes)
```

Then open <http://127.0.0.1:8000> and pick a person. Other commands:

```sh
make up          # start everything, keeping your data
make down        # stop, keeping all data
make reset       # wipe everything (database, workflows, mail, uploads, keys) and load the seed again
make sim         # the same story run as make demo, without opening the browser
```

| Page | Address |
|---|---|
| The app | <http://127.0.0.1:8000> |
| The test mailbox (Mailpit) | <http://127.0.0.1:8025> |
| Workflows (Temporal) | <http://127.0.0.1:8233> |

## What you'll see

`make demo` plays every row of the persona table in [the spec](docs/01-SPEC.md#simulated-people-and-their-expected-end-states)
through the real pages and email links, then prints something like:

```text
  PASS  H1 ends recorded, 3 marks, 1 emails, 0 review items
  PASS  H2 ends recorded, 3 marks, 1 emails, 0 review items
  ...
  PASS  T1 ends replied, final_on_file, 1 emails
  PASS  K1 ends recorded, 3 marks, 1 emails, 0 review items
  PASS  every delivered email (26) has a used approval and an audit row
  PASS  no email went to a suppressed address
  PASS  no page or email contains a banned phrase

20 of 20 checks passed.
```

The full report is saved in `build/demo-report.txt`. Afterwards, sign in as:

| Person | Role | Try this |
|---|---|---|
| **Mike Brennan** | Contractor (Harbor & Pine Builders) | *Import* a CSV and invoices, check the two reads, approve the exact emails, see each job's *Runs* page |
| **Dana Whitfield** | Homeowner (14 Osprey Ln) | *My home*, the *Sale report*, share links for a buyer's agent or inspector, *Ask the town*, *Add work you had done* |
| **Priya Natarajan** | Ops | The *needs a look* list, emails with an unknown result, privacy requests, data health |
| **Marcus Ortega** | Homeowner | A second home, to check that one homeowner can't see another's |
| **Pat Kim** | Contractor (Inlet Roofing) | A second contractor, to check the same for contractors |

Two homes are not yet claimed. Claim one as a new homeowner with the code from its (made-up) letter:
`SAMPLE-4721` or `SAMPLE-5832`.

Time runs fast in the demo (by default one day passes every 5 seconds), so reminders, the 30-day answer
window and the town's 7 business days all happen while you watch. The clock is shown on every page.

## Three ways to read invoices

| Mode | What reads the invoices | Needs |
|---|---|---|
| **replay** (default) | Saved answers from earlier local model runs, matched to each file | Nothing |
| **light** | One local model ([Ollama](https://ollama.com), `qwen3-vl:4b`); every fact goes to a person | About 4.5 GB of memory |
| **full** | Two local models (`qwen3-vl:4b` and `qwen3-vl:8b`) that must agree | About 11 GB of memory |

Set `CLOSEOUT_MODE` in `.env` (see `.env.example`). The Docker services themselves use about 310 MB when idle.

> **Hosted mode warning.** There is also a hosted mode that sends invoice images to an outside AI company.
> It is for made-up documents only. A privacy request can't reach copies that company holds. It is off unless
> you turn it on yourself.

## How it's checked

- `make check`: lint, types, the publish check and about 1,400 fast tests (unit, property, integration with a
  real Postgres, workflow tests with time skipped, saved-history replays, chaos tests for the email sender)
- `make test`: those plus the full-stack and browser tests
- `make eval`: the reading evals in replay mode. Results are in [`evals/reports`](evals/reports) and explained
  in [docs/04-EVALS.md](docs/04-EVALS.md)
- `make mutation`: mutation testing of the core rules (gate: 85% of mutants killed)

The design is in [`docs/`](docs): [spec](docs/01-SPEC.md), [architecture](docs/02-ARCHITECTURE.md),
[design](docs/03-DESIGN.md), [evals](docs/04-EVALS.md), [tests](docs/05-TEST-STRATEGY.md) and
[the build plan with status](docs/06-IMPLEMENTATION-PLAN.md).

## If something goes wrong

| What you see | Fix |
|---|---|
| `make demo` stops at "image does not exist" or a build error | Docker sometimes drops a build. Run `make demo` again. |
| A port is already in use (8000, 8025 or 8233) | Stop whatever uses it, or `make down` an older copy of the demo |
| Some checks fail with timeouts on a slow or busy computer | Close other heavy programs and run `make demo` again. `SIM_SCALE=4320 make demo` slows the clock by half. |
| The pages show an old state | `make reset` |
| Light or full mode says it can't reach the model | Start Ollama. On Linux see "Local models on Linux" below. |
| Anything else | `make logs` shows the last lines from every service |

### Local models on Linux

On a Mac, Docker reaches Ollama at `host.docker.internal`. On Linux, compose maps that name to the host
(`host-gateway`), but Ollama listens only on 127.0.0.1 by default, which containers can't reach. Start it on
the Docker bridge address instead, which is still not reachable from other machines:

```sh
OLLAMA_HOST=172.17.0.1:11434 ollama serve
```

Don't use `0.0.0.0`: that opens Ollama to your whole network. (Replay mode needs no models at all.)

## Known demo limits

This is a demo, not a product. Things it does not do:

- **Sign-in is "pick a person".** There are no passwords. Anyone at the computer can act as anyone.
- **A contractor could enter their own address as the homeowner's** and then confirm their own job. A real
  version would need to check who the homeowner is (for example by the claim letter) before it counts.
- **Emails never leave the computer.** They go to Mailpit. There is no real mail server, bounce handling or
  spam checking.
- **One real town.** Manasquan's permits come from the state's public data, refreshed by a schedule. The
  people and jobs all live in a made-up town, Sampleton.
- **Ops can't re-send an email whose result is unknown.** They can mark it as arrived or close it; sending it
  again is not built.
- **The cost and speed page (S13) is not built.** Model calls are still logged with time and tokens.
- **Business days** skip weekends and NJ state holidays for 2024-2030 only.

## Runs on your computer only

Every service listens on 127.0.0.1, so nothing is reachable from other machines. Emails go only to the local
Mailpit mailbox, and only to made-up `.example` and `.test` addresses.

## Notice

Closeout is a demo built to show an idea. It is not legal, real-estate or building-code advice, and a mark in
it is not a town's or anyone's certification. Every person, company, home, invoice and letter in it is made
up; any likeness to a real one is a coincidence. The permit and parcel data is public data used as published.

## Credits

- Construction permits: NJ Department of Community Affairs, via [data.nj.gov](https://data.nj.gov) (public domain).
- Parcels: NJ Office of GIS (NJOGIS).
- Workflows: [Temporal](https://temporal.io). Test mailbox: [Mailpit](https://mailpit.axllent.org).
  Local models: [Ollama](https://ollama.com) with Qwen3-VL.
- Fonts in the made-up invoices and letters: see [data/SOURCES.md](data/SOURCES.md).

## License

MIT. See [LICENSE](LICENSE).
