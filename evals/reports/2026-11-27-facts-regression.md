# Reading eval: regression set (replay mode)

Run 2026-11-27. Counted per document (n = 30) and per fact (270 facts).

| Measure | Result |
|---|---|
| E1 documents with a wrong kept fact | 0 of 30 (95% upper bound 9.5%) |
| E1 wrong kept facts | 0 of 255 kept |
| E2 asked facts per job | 0.50 |
| Share of facts kept without asking | 94.4% |
| Reader A facts right | 97.8% |
| Reader B facts right | 97.0% |
| Missing cases | 0 |

Reader A by field:

| Field | Right |
|---|---|
| contractor_name | 100% |
| contractor_license | 93% |
| contractor_email | 93% |
| customer_name | 100% |
| job_address | 100% |
| work_type | 100% |
| completion_date | 97% |
| total_amount | 100% |
| warranty_years | 97% |

Worst 5:

- inv26: wrong kept none, asked ['contractor_license', 'contractor_email', 'completion_date', 'warranty_years']
- inv29: wrong kept none, asked ['contractor_email', 'job_address']
- inv02: wrong kept none, asked ['warranty_years']
- inv11: wrong kept none, asked ['contractor_email']
- inv12: wrong kept none, asked ['contractor_email']
