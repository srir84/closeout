# AI model test — what we found

Tested on 26 Nov 2026 on an Apple M3 Max Mac (128 GB) with Ollama 0.40.18. All invoices and people are made up.

## The short answer

- **Pick Qwen3-VL 4B to read invoices.** It was the most accurate model we tested, local or hosted (98% of fields right). It takes about 5 seconds per invoice and uses about 4.5 GB of memory.
- **Have a second model read every invoice too.** Use Qwen3-VL 8B. Every model, including the hosted one, sometimes reads a blurry photo wrong and is sure of it. When the two reads disagree, the app asks the owner. With this check, 0 of 270 facts were wrong, at a cost of 3 questions for 30 invoices.
- **Don't let a small model decide the next step, do date math or apply rules.** Small local models picked the right action only 5–8 times out of 10. All of them got a simple date calculation wrong. The workflow code decides the steps, and the model only reads documents. All outgoing text comes from fixed templates.
- **The vision models ignored the hidden instructions on all 3 test invoices, but 3 is too few to prove much.** The OCR-then-text setup was fooled on 1 of 3. The real plan has 60 attack documents, each with a clean twin (see `docs/04-EVALS.md`).
- **Drop the "OCR first, then a text model" idea.** It lost the company name on dark letterheads and got 70% of fields right.

## What we tested

**Test 1: reading invoices.** 30 made-up invoice images, with 9 facts to pull from each:

- contractor, license number, email, customer, job address, type of work, date finished, total, warranty

The invoices came in four layouts, a mix of clean scans and phone photos, and these traps:

- a deposit, so the "balance due" is not the total
- sales tax
- long street names
- no warranty
- hidden instructions to the AI on 3 invoices
- a hard set:
  - billing address different from the job site
  - change orders
  - very rough photos

**Test 2: choosing the next action.** 10 situations where the model sees the job facts and must pick one action: look up the house, look up permits, draft the email, ask the owner, or do nothing.

The hosted reference was OpenAI's model through the Codex CLI. No paid AI key was needed.

## Test 1 results: reading invoices

| Model | Download | Facts right | All 9 right | Hard set | Ignored hidden instructions | Seconds per invoice |
|---|---|---|---|---|---|---|
| **Qwen3-VL 4B** | 3.3 GB | **98%** | 27/30 | 94% | 3/3 | 4.5 |
| Hosted (Codex) | — | 97% | 27/30 | 91% | 3/3 | 17 |
| Qwen3-VL 8B | 6.2 GB | 97% | 24/30 | 93% | 3/3 | 7.8 |
| Ministral 3 8B | 6.0 GB | 96% | 24/30 | 90% | 3/3 | 4.9 |
| Gemma 4 E4B | 9.5 GB | 96% | 25/30 | 87% | 3/3 | 2.7 |
| Gemma 4 12B | 7.7 GB | 95% | 25/30 | 86% | 3/3 | 4.8 |
| GLM-OCR, then Qwen3 4B | 5.2 GB | 70% | 8/30 | 76% | 2/3 | 4.8 |

Almost every miss came from the rough photos. A person can still read those photos, but the models make up believable values instead:

- a license number with wrong digits
- `john@…` instead of `jobs@…`
- the right customer with a misspelled name
- **a total of $84,100 instead of $4,100**

## Our safety checks

The app never trusts one read:

1. **The contractor's name, license and email come from our own contractor list**, not from the document.
2. **A second model reads the same invoice.** If the two reads disagree on a fact, the owner is asked instead of the fact going on the record.

| Reads | Wrong facts that reach the record | Questions to the owner (30 invoices) |
|---|---|---|
| Qwen3-VL 4B alone | 2 of 270 | 0 |
| Gemma 4 E4B alone | 6 of 270 (one was the $84,100 total) | 1 |
| Hosted alone | 3 of 270 | 0 |
| **Qwen3-VL 4B + Qwen3-VL 8B** | **0 of 270** | **3** |
| Qwen3-VL 4B + Gemma 4 E4B | 0 of 270 | 8 |
| Qwen3-VL 4B + hosted | 0 of 270 | 3 |

The homeowner also confirms every job before it's recorded, which is a third check.

## Test 2 results: choosing the next action

| Model | Right action | Common mistakes |
|---|---|---|
| Hosted (Codex) | 9/10 | Asked the owner when nothing was needed (a cautious mistake) |
| Qwen3-VL 4B | 8/10 | Date math wrong. Didn't ask when the house had been sold. |
| Qwen3-VL 8B | 8/10 | Picked the wrong lookup. Acted when nothing was needed. |
| Ministral 3 8B | 8/10 | Date math wrong |
| Gemma 4 E4B | 8/10 | Date math off by a day. Looked up permits for a roof job (roofs need no permit). |
| Qwen3 4B (text only) | 7/10 | Date math wrong. Slow (16 s). |
| Gemma 4 12B | 7/10 | Asked the owner instead of drafting a ready email |
| Qwen3 8B (text only) | 5/10 | Often did nothing when it should have acted |

**What this means for the design**
- Every local model got the date math wrong. Code works out dates, not the model.
- Rules like "a roof needs no permit" live in code, not in the prompt.
- The step-by-step flow (find the house → check permits → queue the email → wait) is fixed in the workflow. The model doesn't choose the path and doesn't write the emails.
- No model sent anything to the address planted in the document. Two models mentioned it to the owner, which is allowed.

## What this means for "anyone can run it"

| Setup | What you need | What you get |
|---|---|---|
| **Replay** (no AI) | Nothing extra | Saved answers from this test play back. Works on any computer. |
| **Light** | Ollama + Qwen3-VL 4B (3.3 GB download, about 4.5 GB memory) | Real reading. The owner confirms each job. |
| **Full** | Add Qwen3-VL 8B (6.2 GB more download, about 11 GB memory for both) | Second read, so fewer wrong facts get through |
| **Hosted** | An AI provider key | Optional comparison of cost and speed |

**A note on these numbers.** These 30 invoices were used to pick the models, so they can't also prove how good the pick is. In the build they become a regression set. A separate held-out set of 300 invoices, never used to choose anything, measures quality (see `docs/04-EVALS.md`).

## Things to keep in mind

- **The invoices are made up.** Real invoices are messier: several pages, handwriting, stains. The build adds 30 generated messy invoices (see `spikes/messy-docs/RESEARCH.md`).
- **The test is small.** The 270 facts come from 30 invoices, and facts on the same invoice fail together. Counted per invoice, 0 wrong out of 30 only shows the rate is probably under 1 in 10. These invoices also picked the models. The held-out set in the build gives the real number.
- **Speed depends on the computer.** These times are from a fast Mac. A regular laptop will be slower.
- **Gemma 4 needs a workaround.** On this Ollama version it can't be held to a fixed answer format, so we ask for JSON in plain words and check the answer.
- **The hosted model ran through a different tool.** It ran through the Codex CLI, not a direct API call, so its times aren't comparable.

## Files

| File | What it is |
|---|---|
| `make_docs.py` | Makes the 30 sample invoices and the right answers (`truth.json`) |
| `run_extract.py` | Runs a model over every invoice and saves its raw answers |
| `score.py` | Scores the answers, writes `results/summary.md` with every miss |
| `crosscheck.py` | Applies the safety checks, writes `results/crosscheck.md` |
| `run_tools.py` | Test 2, writes `results/tools.md` |
| `results/raw/` | Every model answer from this test. The build records its own saved answers with `make record`, because the replay key needs the exact request. |

To rerun:

```
ollama pull qwen3-vl:4b-instruct
python3 make_docs.py
python3 run_extract.py qwen3-vl:4b-instruct
python3 score.py
```
