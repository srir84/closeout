# Reading eval: messy set (replay mode)

Run 2026-11-27. Counted per document (n = 30) and per fact (270 facts).

| Measure | Result |
|---|---|
| E1 documents with a wrong kept fact | 5 of 30 (95% upper bound 31.7%) |
| E1 wrong kept facts | 5 of 259 kept |
| E2 asked facts per job | 0.37 |
| Share of facts kept without asking | 95.9% |
| Reader A facts right | 95.9% |
| Reader B facts right | 95.9% |
| Missing cases | 0 |

Reader A by field:

| Field | Right |
|---|---|
| contractor_name | 100% |
| contractor_license | 100% |
| contractor_email | 97% |
| customer_name | 100% |
| job_address | 97% |
| work_type | 100% |
| completion_date | 87% |
| total_amount | 83% |
| warranty_years | 100% |

Worst 5:

- messy11: wrong kept ['total_amount'], asked ['completion_date']
- messy29: wrong kept ['total_amount'], asked ['contractor_email']
- messy05: wrong kept ['total_amount'], asked none
- messy17: wrong kept ['total_amount'], asked none
- messy23: wrong kept ['total_amount'], asked none
