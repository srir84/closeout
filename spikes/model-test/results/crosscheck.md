# After the safety checks

Contractor details come from our contractor list. Other fields are kept only when two reads agree;
otherwise the owner is asked. "Wrong facts recorded" is what would reach a home record unchecked.

| Reads | Invoices | Wrong facts recorded | Owner questions | Questions per invoice | Questions where the first read was right |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct + qwen3-vl:4b-instruct | 30 | 2 of 270 | 0 | 0.0 | 0 |
| qwen3-vl:4b-instruct + gemma4:e4b | 30 | 0 of 270 | 8 | 0.27 | 6 |
| qwen3-vl:4b-instruct + qwen3-vl:8b-instruct | 30 | 0 of 270 | 3 | 0.1 | 1 |
| gemma4:e4b + gemma4:e4b | 30 | 6 of 270 | 1 | 0.03 | 1 |
| codex + codex | 30 | 3 of 270 | 0 | 0.0 | 0 |
| qwen3-vl:4b-instruct + codex | 30 | 0 of 270 | 3 | 0.1 | 1 |

## Wrong facts that got through

**qwen3-vl:4b-instruct + qwen3-vl:4b-instruct**: inv26 completion_date: recorded '2023-03-15', truth '2025-03-07'; inv26 warranty_years: recorded 8, truth 5

**qwen3-vl:4b-instruct + gemma4:e4b**: none

**qwen3-vl:4b-instruct + qwen3-vl:8b-instruct**: none

**gemma4:e4b + gemma4:e4b**: inv25 job_address: recorded '25 Quill Ave', truth '25 Gull Ave'; inv25 completion_date: recorded '2025-09-30', truth '2025-08-30'; inv26 completion_date: recorded '2025-03-15', truth '2025-03-07'; inv29 completion_date: recorded '11-Aug-26', truth '2026-08-11'; inv30 customer_name: recorded 'Victor Alonao', truth 'Victor Alonzo'; inv30 total_amount: recorded 84100.0, truth 4100.0

**codex + codex**: inv26 completion_date: recorded '2025-05-07', truth '2025-03-07'; inv26 warranty_years: recorded 6, truth 5; inv30 customer_name: recorded 'Victor Akano', truth 'Victor Alonzo'

**qwen3-vl:4b-instruct + codex**: none
