# Tool-use test — results

10 cases. The model sees typed facts and must make the single right call.

| Model | Passed | Median seconds | Failures |
|---|---|---|---|
| codex | 9/10 | 10.51 | t07: should do nothing, called ask_owner |
| gemma4:12b | 7/10 | 0.93 | t03: called ask_owner instead of propose_confirmation_email; t07: should do nothing, called find_permits; t10: should do nothing, called ask_owner |
| gemma4:e4b | 8/10 | 0.38 | t02: from_date='2026-03-28', want '2026-03-29'; t07: should do nothing, called find_permits |
| ministral-3:8b | 8/10 | 1.08 | t02: from_date='2025-03-25', want '2026-03-29'; t07: should do nothing, called ask_owner |
| qwen3-vl:4b-instruct | 8/10 | 0.45 | t02: from_date='2025-09-25', want '2026-03-29'; t09: expected 1 call to ask_owner, got 0 |
| qwen3-vl:8b-instruct | 8/10 | 0.94 | t02: called find_parcel instead of find_permits; t07: should do nothing, called propose_confirmation_email |
| qwen3:4b | 7/10 | 15.84 | t02: from_date='2026-03-25', want '2026-03-29'; t07: should do nothing, called ask_owner; t10: should do nothing, called ask_owner |
| qwen3:8b | 5/10 | 0.99 | t02: from_date='2026-03-25', want '2026-03-29'; t03: expected 1 call to propose_confirmation_email, got 0; t05: expected 1 call to ask_owner, got 0; t07: should do nothing, called propose_confirmation_email; t09: expected 1 call to ask_owner, got 0 |
