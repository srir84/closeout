# 1. Spec — what Phase 1 must do

This is the "what", not the "how". Every requirement has an ID (like `R-OUT-2`) and the slice that builds it. Tests carry the same ID, so we can always check that every requirement is tested ([test strategy](05-TEST-STRATEGY.md)).

Plain-English summary of the product: [PROJECT_BRIEF.md](../PROJECT_BRIEF.md). Clickable mock: [pages/closeout-prototype.html](../pages/closeout-prototype.html).

## Who uses it

| Person | Sample name | Uses it to |
|---|---|---|
| Contractor | Mike (Harbor & Pine Builders) | Turn past jobs into verified records and a profile |
| Homeowner | Dana (14 Osprey Ln, Sampleton) | Confirm work, see the record, share a sale-ready report |
| Buyer's agent / buyer / inspector | Tom | Read a shared report |
| Operations | Priya | Handle what the system won't guess, privacy requests, data health |

Every name, company and address above is made up.

## Two towns: one real, one made up

| | **Manasquan (real, code 1329)** | **Sampleton (made up, code 9999)** |
|---|---|---|
| What's in it | Real public permits and parcels | Made-up parcels, permits, people and jobs, made by a seeded script |
| Where you see it | The data pages: permit feed, match rates, data health | Every story in the demo: import, emails, marks, report, asking the town |
| People | None, ever | All made-up people live here |
| Label | "Real public data" | "Sample town — made up" on every page |

This is how the demo can show all 3 trust marks without ever putting a made-up person on a real house.

## Scope

**In Phase 1:**
- The permit feed
- Contractor import
- Reading invoices
- Permit match
- Approvals
- Emails to homeowners
- Homeowner confirm
- Trust marks
- Undo
- Sale report
- Asking the town
- Ops queue
- Privacy requests
- Run-it-yourself packaging

**Not in Phase 1** (see the roadmap in the brief):
- Reading separate warranty documents (the report shows the warranty years read from the invoice)
- Reminders for upkeep
- Contractor search
- Quotes
- Payments
- Real email delivery
- Real towns other than Manasquan
- Mobile apps
- Logins beyond a simple demo sign-in
- AI-written emails (all outgoing text comes from fixed templates)

## Requirements

Each line is written so a test can prove it. "Must" means the release is blocked without it. The **Slice** column says which slices of the [implementation plan](06-IMPLEMENTATION-PLAN.md) build it. Each test carries both tags, for example `req("R-OUT-1")` and `slice("S6")`. When a slice is marked done, every requirement that lists it must have at least one passing test tagged with that pair.

### Data (`R-DATA`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-DATA-1 | Load Manasquan permits from data.nj.gov (dataset `w9se-dmra`, `treasurycode = 1329`). Each source row is kept by its own ID (`pk`). Each run fetches the whole town (about 2,914 rows), then adds new rows, updates changed ones and flags ones that disappeared. Nothing is saved unless every page arrived. A permit that gets its final approval adds one "final recorded" event. | Fake-API test: load 100 rows, then serve 5 new, 1 changed (final approval added) and 1 missing. Exactly 5 inserts, 1 update, 1 flagged, 1 "final recorded" event. A run with a failed page saves nothing. | S1, S2 |
| R-DATA-2 | Load the Manasquan parcel table (block, lot, qualifier, street address, property class, year built, map point) from a small seed file. A script rebuilds the seed from the state downloads, and the file's checksum is recorded. | Exact counts on the snapshot: 3,982 parcels, 3,362 with a street address. | S1 |
| R-DATA-3 | Match each permit to a parcel by block and lot. Exactly one parcel → linked. Several or none → not linked, with the reason saved. | On the snapshot: exactly 2,712 linked, 82 several, 120 none (counted before the build), and exact agreement with `tools/join_check.py` row by row. | S1 |
| R-DATA-4 | Bad dates (outside 1950 to today + 1 year) are stored as empty and flagged, never guessed. | Test with named made-up bad rows (Manasquan has none today). | S1 |
| R-DATA-5 | Show where each data source came from and credit it (state of NJ, NJOGIS). | The data page and the README show the credits. A test checks the footer. | S1 |
| R-DATA-6 | The sample town (Sampleton) is made by a seeded script and is clearly labelled. No Sampleton address matches a real Manasquan address. Real-data counts only ever count town 1329. | A test runs the script twice and gets the same files. Another test checks that no Sampleton address matches a Manasquan one. The label appears on every Sampleton page. | S1 |

### Contractor import (`R-IMP`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-IMP-1 | A contractor can upload past jobs as a CSV file (fixed columns, see design 3.16) plus one invoice file per job (PNG, JPG or PDF up to 3 pages). Files over the limits are refused with a clear message. | Browser test uploads a CSV and 3 files. Tests for oversized, wrong-type and too-many-pages files. | S4 |
| R-IMP-2 | Each job address is matched to one parcel: exact match after cleanup, or one close match on the same street **with the same house number and unit**. Several matches (for example condos) or none → "needs a look", never guessed. | Golden cases: exact, abbreviated, condo unit, unknown, and **neighbour houses** (128 vs 126 Tidewater Rd must not match). | S4 |
| R-IMP-3 | In full mode, each invoice is read by two different models. A fact is kept only when both reads agree after cleanup. Otherwise the person is asked. In light mode (one model), every model-read fact stays unconfirmed until the person approves it on one review screen. | Held-out eval: 0 wrong facts kept (see evals 4.1, E1). Light-mode test: no fact is kept without a person's approval. | S3, S4 |
| R-IMP-4 | The contractor's name, license and email come from our contractor list, never from the document. If the invoice names a different contractor from the uploader → "needs a look". | Test: an invoice with a look-alike email or another contractor's license never changes a stored contact and opens a review item. | S4 |
| R-IMP-5 | Dates, money and license numbers are checked and cleaned by code (format, range), not trusted from the model. | Property tests on the cleaners | S3 |
| R-IMP-6 | *(Can be cut.)* A homeowner can upload an invoice for their own home, pick the contractor from our list and ask them to confirm. | Workflow + browser test | S9 |

### Permit match (`R-COR`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-COR-1 | For each job, look for permits on the same parcel issued between 365 days before and 14 days after the finish date, with the right type and **the matching trade** (building, plumbing, electrical or fire, from the permit's fee columns). The four results are in design 3.2. Exactly one → the mark "**Town permit on file**", shown as "A town permit with plumbing work was issued for this address on 12 Mar 2025". Several → the contractor picks one, or it goes to "needs a look". A permit with no trade data → "State data doesn't show what work it covered", with no mark. None → "No matching permit found", a plain statement. | Rule table tests, including permits with no fee data. False-match eval: made-up jobs of one trade on parcels whose only permit is another trade never get the mark (evals 4.3). | S5 |
| R-COR-2 | We never claim a permit wasn't needed. For a roof or window job with no permit found, if the contractor stated it was a same-size replacement on a detached one- or two-family home, the page shows that statement as the contractor's, plus "These usually don't need a permit (state rules)". Property data never turns this on. | Tests: with and without each statement, and structural roof work stated as not same-size. | S5 |
| R-COR-3 | The product never says "open permit", "unpermitted", "illegal" or "violation", and never suggests unfinished work. Missing final approval is shown as "Final approval not in state data". | A scan of **every rendered page and every outgoing email** in the browser and simulator runs, and a check inside the send step. | S5, S7 |

### Approvals and sending (`R-OUT`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-OUT-1 | Nothing is sent until the account that sends it approves it. The approval page shows every message in the sequence (first email and each reminder) with its exact subject, words and recipient address. Each message gets its own approval. It works once, expires, and is tied to the job's round and content version. The address is fixed at approval: a later contact edit only affects a new round. | Tests for each refusal reason in design 3.6: changed words, subject, recipient, step, round, account or content, an expired, reused, voided or unknown approval, and an approval for another job. | S6, S7 |
| R-OUT-2 | Each approved message is delivered at most once, even if any part of the system crashes at any moment. If we can't tell whether it went, it's marked "unknown" for ops and never re-sent automatically. With no crash, every message is delivered. | Crash test over a fixed list of crash points, each with its exact expected end state (design 3.7). A sender that never sends fails it. | S6 |
| R-OUT-3 | The homeowner gets a link to confirm, correct, decline or say "not my house", and chooses whether the price can be shown. "Not my house" stops all further emails for that job and opens a review item. | Browser test of all 4 answers | S7 |
| R-OUT-4 | If there's no answer, at most 2 reminders: 3 and 7 days after the first email was **delivered**, then a last wait of 7 days, then "no answer". A late answer still counts within 30 days of the first delivery. | Time-skipping workflow test for each path, including an answer on day 20 and one on day 31. | S7 |
| R-OUT-5 | Anyone can unsubscribe from a link in every email (one click, no sign-in). Unsubscribing cancels every queued message to that address at once. A message already being handed to the mail server at that moment may still arrive. The app says so. | Test: unsubscribe while a reminder is queued, and the reminder is cancelled. A race test shows the only possible extra message is one already claimed. | S7 |
| R-OUT-6 | Recipients come only from the recipient list: people the account owner typed in, our contractor list, or the town's clerk address from the town settings. Never from a document. | Attack set: 0 sends to planted addresses. A code check finds no other source. | S6 |
| R-OUT-7 | Demo emails can only go to the local test mailbox (Mailpit), and only to made-up domains (`.example`, `.test`). Anything else is refused. | Tests: a real mail server makes startup fail, and a real-looking address is refused at send. | S0, S6 |

### Home record (`R-REC`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-REC-1 | The record is a list of events that are only ever added, never edited. Each event has an order number per job and its own ID, so a retried step can't add it twice. What the page shows is worked out from the events. | Property tests: adding the same event twice changes nothing. A job's status always equals what the events say. A "recorded" job always has a homeowner confirmation before it. | S2 |
| R-REC-2 | A recorded job shows up to 3 marks: homeowner confirmed, contractor confirmed, town permit on file. Each mark links to its proof (design 3.9). Marks from an undone round never show. | Unit tests per mark, and a browser check that each mark's link opens its proof. | S2, S8 |
| R-REC-3 | Undo: the contractor can undo a confirmation for 10 minutes (60 seconds in the fast-clock demo). Undo hides that round's marks and puts the job back to "ready" for a new round, with no automatic resend. After the window, only the contractor's "retract" is possible: it hides the job and says so. Emails already sent can't be unsent, and the app says this. | Workflow tests: undo in time, too late, an undo pressed in time but delivered late, retract, and an old-round link used after an undo. | S8 |
| R-REC-4 | A home's owner comes only from the sample-town seed or from a claim with a code from a letter (simulated). Answering through an email link shows that one job only, and never gives access to the home. A second claim on an owned home opens a review item. | Tests: answer links, including "not my house", give no home access. Claim with a right and a wrong code. Second claimant. | S10 |

### Sale report (`R-REP`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-REP-1 | The owner can make a one-page report of verified work, warranty years and permit history. | Snapshot test of the report | S10 |
| R-REP-2 | The owner shares it by link with a role (buyer's agent, buyer, inspector). Each role sees only what the access table (design 3.12) allows. Prices show only if the owner turned them on **and** the homeowner allowed it. Original files (invoices, town letters) are never shared in Phase 1. | Tests call the real pages and downloads for every role and field, with other accounts, guessed IDs and revoked links. | S10 |
| R-REP-3 | Links expire and can be revoked. A revoked or expired link shows nothing. | Test | S10 |

### Ask the town (`R-TOWN`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-TOWN-1 | For permits on the owner's home with no final approval in state data and older than 12 months, offer to ask the town. | Test with sample-town permits | S11 |
| R-TOWN-2 | Draft a public records request and one reminder from fixed templates. The owner approves both (R-OUT-1). It's sent to the town's clerk address (the town simulator in the demo). The system waits 7 business days, reminds once, then waits 5 more business days, and still accepts a late reply for 60 days. The clerk answers by uploading a document through the link in the email. A result is shown only if both reads agree on it and its date. Otherwise it's "unclear" and goes to ops. The report shows the result with the town as the source, or "no reply". | Time-skipping workflow tests (reply, late reply, no reply) and an end-to-end run with the town simulator, including its "never replies" case. Reply eval: 0 wrong results shown. | S11 |

### Operations and privacy (`R-OPS`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-OPS-1 | Everything the system won't guess lands in one "needs a look" list with the reason and the proof. | Browser test | S4, S12 |
| R-OPS-2 | A privacy request removes a person's name and contact details from every place they're stored (design 3.17), adds them to the do-not-contact list, stops anything still running for them from writing their details again, and logs it without the details. | **Canary test** (design 3.17): a unique made-up name and email go through the whole flow, including a correction, a failed step, an undelivered click and a late model answer. After the request they appear nowhere: database dump, uploads, Temporal history export, Mailpit, container logs, traces. | S12 |
| R-OPS-3 | Every action the system takes is in an audit log: who asked, who approved, what happened. | Test after every simulator run: every delivered email has a used approval and an audit row. | S6 |

### Safety (`R-SAFE`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-SAFE-1 | Text from documents only ever goes to a model with no tools. Its output is typed fields only, and cleaned values can't contain links or control characters. | Code check: the reader can't call tools. Attack set (evals 4.2). | S3 |
| R-SAFE-2 | Every action (send, share, town request) goes through one permission check that is safe to retry. | Test: calling an action without it fails. A code scan finds no direct writes. A retried action returns the first result, even after its approval expired. | S6, S10 |
| R-SAFE-3 | Links in emails and share links can't be guessed, expire, only work for their one purpose and round, and opening one never changes anything (only pressing a button does). | Tests: a link for one purpose is refused at every other page. Expiry at exactly the boundary. A plain page load changes no data. | S7, S10 |
| R-SAFE-4 | Everything runs on your own computer only. No service is reachable from other machines. The one exception is hosted mode, which you turn on yourself: it sends document images to an outside AI company, so it's for made-up documents only. | Test of the resolved Docker settings: every published port is on 127.0.0.1. The README states the hosted-mode exception. | S0 |

### Run it yourself (`R-RUN`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-RUN-1 | From a fresh copy of the public repo, `make demo` runs the full demo in replay mode (no AI downloads, no keys) in under 10 minutes on a computer with Docker, and ends by printing a pass/fail list of what it checked. | CI job on a clean Linux machine using the exact public files. Manual check on a Mac. | S14 |
| R-RUN-2 | One setting picks the mode: replay, light (1 local model), full (2 local models) or hosted. | Tests per mode. Live modes run on the owner's Mac (test strategy 5.5). | S3 |
| R-RUN-3 | A fast-clock setting makes waiting days take seconds. Windows where a person must act get a floor in real seconds. The UI shows a "fast clock" banner while it's on. | Tests (design 3.3, the clock table) | S7b |
| R-RUN-4 | `make reset` puts everything back to the start, including Temporal, Mailpit and uploads. | Test: reset, then demo, twice in a row, with the same end-state summary. | S0, S14 |
| R-RUN-5 | The README gives a 10-minute quickstart, what you'll see, a fix-it list, the data credits and a "made-up people, not tied to any company" notice. | A doc check in CI, plus a person following it once per milestone | S14 |
| R-RUN-6 | Stopping and starting the whole stack (`make down && make up`) loses no waiting workflow and no delivered email. | Test at the restart-test clock speed (design 3.3): restart everything while a job waits for a reminder. The reminder still goes out once, and Mailpit still has the first email. | S0, S7 |

### Watching it work (`R-OBS`)

| ID | Requirement | How we'll know | Slice |
|---|---|---|---|
| R-OBS-1 | Every model call is logged with the reader, model, model version, time taken, tokens and cost (zero for local). | Test | S3 |
| R-OBS-2 | A page shows each job's steps, waits and decisions (the "agent runs" view in the mock), to the people allowed to see that job. It shows no email text, addresses or prices. | Browser test, including access by another account | S8 |
| R-OBS-3 | *(Can be cut.)* A page compares cost and speed per mode. Optional Phoenix traces carry IDs and timings only. | Test | S13 |

**47 requirements.** Two can be cut (R-IMP-6, R-OBS-3). Cutting one removes it from this list in a reviewed change. It's never just skipped.

## Simulated people and their expected end states

The simulators follow these exactly (fixed seeds), and `make sim` and `make demo` check every row. All of them live in Sampleton. Each homeowner's job is set up with one matching town permit, unless noted.

| # | Person | What they do | Job ends | Marks shown | Emails they get | Review items | Slice |
|---|---|---|---|---|---|---|---|
| H1 | Confirms at once | Confirm after the first email | `recorded` | 3 | 1 | 0 | S7b |
| H2 | Fixes the date | Correct the date. The contractor bot accepts. | `recorded` | 3 | 1 | 0 | S7b |
| H3 | Fixes the price | Correct the price. The contractor bot rejects. | `needs_a_look` | 0 | 1 | 1 | S7b |
| H4 | Answers after reminder 1 | Confirm after reminder 1 | `recorded` | 3 | 2 | 0 | S7b |
| H5 | Answers after reminder 2 | Confirm after reminder 2 | `recorded` | 3 | 3 | 0 | S7b |
| H6 | Answers late | Confirm on virtual day 20 | `recorded` | 3 | 3 | 0 | S7b |
| H7 | Declines | Decline | `stopped` | 0 | 1 | 0 | S7b |
| H8 | Not their house | "Not my house" | `stopped` | 0 | 1 | 1 | S7b |
| H9 | Unsubscribes | Unsubscribe after the first email | `no_answer` | 0 | 1 | 0 | S7b |
| H10 | Never answers | Nothing | `no_answer` | 0 | 3 | 0 | S7b |
| H11 | Job with no permit (no trade data on the parcel's permit) | Confirm at once | `recorded` | 2 | 1 | 0 | S7b |
| H12 | Undo | Confirm. The contractor bot undoes, approves again, and H12 confirms again. | `recorded` (round 2) | 3 | 2 | 0 | S8 |
| T1 | Clerk with a record | Replies on business day 4: final approval on file | `replied`, `final_on_file` | — | 1 | 0 | S11 |
| T2 | Clerk with no record | Replies after the reminder: no record found | `replied`, `no_record` | — | 2 | 0 | S11 |
| T3 | Clerk who never replies | Nothing | `no_reply` | — | 2 | 0 | S11 |
| K1 | Contractor asked by a homeowner | Confirms through the link | `recorded` | 3 | 1 | 0 | S9 |

The demo's pass/fail list is these rows plus:
- no email went to a suppressed address
- every delivered email has a used approval and an audit row
- no page or email contains a banned phrase

## Things we're assuming

- The state permit data keeps its current shape. A nightly check alerts us if the columns change.
- One real town (Manasquan) plus one made-up town is enough to prove the idea. Adding a town is a settings entry plus that town's files.
- A simple demo sign-in (pick a person) is fine. Real accounts come later.
- Emails are never really delivered. Answers come through links in the emails, from the simulators or from you clicking in Mailpit.
