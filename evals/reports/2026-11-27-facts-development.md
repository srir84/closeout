# Reading eval: development set (replay mode)

Run 2026-11-27. Counted per document (n = 100) and per fact (900 facts).

| Measure | Result |
|---|---|
| E1 documents with a wrong kept fact | 0 of 100 (95% upper bound 3.0%) |
| E1 wrong kept facts | 0 of 842 kept |
| E2 asked facts per job | 0.58 |
| Share of facts kept without asking | 93.6% |
| Reader A facts right | 99.7% |
| Reader B facts right | 93.9% |
| Missing cases | 0 |

Reader A by field:

| Field | Right |
|---|---|
| contractor_name | 100% |
| contractor_license | 100% |
| contractor_email | 100% |
| customer_name | 100% |
| job_address | 100% |
| work_type | 100% |
| completion_date | 100% |
| total_amount | 97% |
| warranty_years | 100% |

Worst 5:

- dev009: wrong kept none, asked ['contractor_email', 'total_amount']
- dev100: wrong kept none, asked ['contractor_email', 'total_amount']
- dev001: wrong kept none, asked ['contractor_email']
- dev002: wrong kept none, asked ['contractor_email']
- dev003: wrong kept none, asked ['contractor_email']
