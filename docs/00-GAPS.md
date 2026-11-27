# 0. Gap log — what was missing and where it stands

Status key:
- **Closed**: checked, with proof below.
- **Decided**: a choice was made and written into the design.
- **Planned**: needs building, and it's in a slice of the [implementation plan](06-IMPLEMENTATION-PLAN.md).
- **S0 proves**: a small working test in slice S0 settles it before anything relies on it.

The plan was checked by two independent AI reviewers, Fable and Codex, twice each:
- **Round 1:** 36 + 40 findings, on 26–27 Nov 2026
- **Round 2:** 30 + 19 new findings, plus a status check on every round-1 finding, on 27 Nov 2026

Both rounds are fixed in these docs. "What the reviews changed" lists what moved.

## Data

| Gap | Status | What we found or decided | Where |
|---|---|---|---|
| Street addresses | **Closed** | The county tax list joins the parcel map on the parcel ID for 3,362 of 3,374 tax rows. **2,706 of 2,914 permits (92.9%) get exactly one street address** on the S1 snapshot of 27 Nov 2026 (the research check counted 2,707 on an earlier download). Owner names are blank in all 3,374 rows. | Design 3.18 |
| Permit-to-parcel link | **Closed** | By block and lot: 2,712 permits link to one parcel, 82 to several, 120 to none. These are now fixed expected numbers. | Spec R-DATA-3 |
| Permit row identity | **Closed** | Permit numbers repeat (2,827 distinct in 2,914 rows). The source `pk` is unique, so we key on it. | Design 3.1 |
| Keeping permits up to date | **Decided** | The state publishes in monthly batches, so a date cursor could miss one. Each run fetches the whole town (about 2,914 rows, under 1 MB) and compares row by row. | Spec R-DATA-1. Design 3.8 |
| What work a permit covers | **Closed** | The fee columns show the trade. Of 2,320 alterations: 852 electrical only, 262 building only, 191 plumbing only, 97 fire only. **280 permits have no fee data at all**, so they get their own result: "State data doesn't show what work it covered". | Spec R-COR-1. Design 3.2 |
| "No permit needed" | **Decided** | We never claim it. For roof and window jobs, we show the contractor's own statement plus "these usually don't need a permit". Property class can't tell a detached house from a 4-family one, so it's never used. | Spec R-COR-2 |
| Data terms | **Closed** | Permits: public domain (NJ Dept. of Community Affairs). Parcels: NJ Office of GIS asks for credit. We credit both, and keep no owner names, mailing addresses or sale prices. | Spec R-DATA-5 |
| Download links | **Closed** (parcels, permits). **S1 confirms** (tax list) | `geoapps.nj.gov/njgin/parcel/parcels_shp_dbf_Monmouth.zip` and data.nj.gov `w9se-dmra`. The tax list's source gets recorded in `data/SOURCES.md`. | Slice S1 |
| Real houses vs made-up people | **Decided** | A made-up town, **Sampleton**, holds every made-up person, job and permit. Real Manasquan data appears only on the data pages, with no people. | Spec "Two towns" |
| Sample documents | **Decided** | All made by generator scripts, with no hand work: 300 held-out invoices (100 development, 200 sealed), 30 messy invoices, 60 attack twins, 30 town replies. | Test strategy 5.6, `spikes/messy-docs/` |

## AI model

| Gap | Status | What we found or decided | Where |
|---|---|---|---|
| Which local model | **Closed** | Qwen3-VL 4B reads, and Qwen3-VL 8B is the second read: 0 wrong of 270 facts on the 30 test invoices. This set picked the models, so it's now a regression set. A sealed set measures quality. | `spikes/model-test/RESULTS.md`, Evals 4.0 |
| Should a model choose the next step? | **Decided: no** | Small models chose right only 5–8 times in 10 and all failed date maths. The workflow code decides. | Architecture rule 5 |
| AI-written emails, Agents SDK, MCP | **Decided: not in Phase 1** | Fixed templates, a thin model gateway, MCP later | Spec scope, Design 3.10 |
| Reproducible model runs | **Decided** | Frozen schema and prompts, pinned model digests. The replay key uses the uploaded file's hash and the reader. | Design 3.10 |
| Many reads at once | **Decided** | Model reads have their own queue, one at a time per reader, so a 50-job batch waits instead of timing out | Design 3.8 |
| Where live-model evals run | **Decided** | On the owner's Mac. GitHub's machines have no graphics chip. | Evals 4.7 |
| Hosted mode and privacy | **Decided** | Hosted mode sends images to an outside company. It's opt-in, for made-up documents only, and the README says so. | Spec R-SAFE-4 |

## Running it

| Gap | Status | What we decided | Where |
|---|---|---|---|
| Temporal and Mailpit keep their data | **Decided, S0 proves** | Both save to Docker volumes, so `make down && make up` loses nothing | Design 3.19 |
| Only on your computer | **Decided** | Every port on 127.0.0.1, a host allow-list for the demo sign-in | Design 3.19 |
| How clicks reach workflows | **Decided, S0 proves** | Save the click, then send a Temporal update whose ID is the click's ID. Duplicates return the first result, and a sweep re-sends. | Design 3.5 |
| Sending safely | **Decided** | One approval per message, the address fixed at approval, a claim that re-checks the job, an SMTP stage table and a crash table | Design 3.6, 3.7 |
| Time | **Decided** | One virtual clock for every workflow and bot. Security expiries use real database time when checked. | Design 3.3 |
| Personal data | **Decided** | One table of people, IDs only in workflow histories and add-only records, an erase marker that blocks late writes, a canary test | Design 3.17 |
| Do the test tools work here? | **S0 proves** | Time-skipping, updates, Replayer, Testcontainers, Playwright and mutmut each get a small proof | Plan S0 |
| Outages | **Decided** | Database steps retry without limit, and a startup check finds jobs with no workflow | Design 3.8 |

## GitHub and sharing

| Gap | Status | What we decided | Where |
|---|---|---|---|
| Keeping private notes private | **Closed** (27 Nov 2026) | The research notes and the first plan page now live in a private folder outside the project. The repo will be public from day one. A check for private names runs before every push, using a list kept in that private folder. | Plan S0 |
| Agents can't weaken the checks | **Decided** | The agent has its own GitHub account and works from a fork. The `main` ruleset requires the person's fresh approval. One trusted `gate` check never runs pull-request code. `CODEOWNERS` covers tests, evals, tools, CI, `Makefile`, the spec and the status table. | Test strategy 5.4 |
| Cost of GitHub | **Closed** | A public repo gets rulesets and Actions minutes free | Test strategy 5.5 |
| License, README, credits, Pages, demo video | **Planned** | | S0, S14 |

## What the reviews changed

| Area | Before | Now |
|---|---|---|
| Permits | Keyed on permit number. Matched any "Alteration". Fetched by date. | Keyed on `pk`. Matched by trade, with a result for permits with no trade data. A full fetch each run. |
| Demo story | Made-up people who couldn't match real parcels | Sampleton, plus a table of every simulated person's exact end state |
| Approvals | One approval for an email and its reminders | One per message, with the address fixed at approval, round and content version, and every refusal reason listed |
| Clicks | Unclear how a click reaches a workflow | A command protocol: save, update with the same ID, duplicate check first, sweep |
| Workflows | A child "outreach" workflow with no way to receive messages | Outreach is code inside `Job` and `TownRequest`, and every state has exits |
| Sender | A do-nothing sender passed the crash test | An SMTP stage table, retry spacing, a crash table with exact results, and a fake mail server for faults |
| Time | Each workflow had its own clock, and some waits were 35 ms in the demo | One clock row, real-time floors, a slower speed for restart tests |
| Privacy | Typed values reached workflow histories, and some stores were missing | IDs only in histories, a full inventory, an erase marker, and a canary covering logs and late writes |
| Home access | Anyone answering an email got the home | Only the seed or a letter code. An email answer shows that one job. |
| Evals | One held-out set, run on every pull request | Development (in CI) and sealed (on the Mac only) sets, a miss rule, and attack judging that doesn't punish safe answers |
| Gates | GitHub settings that don't exist on a free private repo | A public repo, an agent fork, a ruleset checklist, and one trusted check |
| Plan | 28, then 41 days | 45.5 days ≈ 9 weeks + a buffer week = 10. S7 split in two. Each table built in the slice that first needs it. |

## Decisions made (27 Nov 2026)

| Question | Decision |
|---|---|
| How long a Phase 1? | **Full plan**, about 10 weeks with all slices. Re-estimated after S0. |
| Agent's GitHub account | **Yes.** A second GitHub account for the coding agent, signed up in S0 with the project's sign-up email (kept outside the repo). |
| Messy invoices | **Made by generator scripts, no hand work.** Research and a working prototype: `spikes/messy-docs/`. |
| Public repo from day one | **Yes.** It makes the GitHub guards and CI minutes free. The private material has already been moved out. |

## Still open (decide later, not blockers)

| Question | When to decide |
|---|---|
| Keep S9 and S13, or cut them | After S0's re-estimate, and again at M2 |
| Public repo name | Before S0 |
