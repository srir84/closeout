# Closeout demo. Everything runs on this computer (ports on 127.0.0.1 only).
-include .env
COMPOSE := docker compose -f deploy/compose.yaml
SCALE ?= $(or $(CLOSEOUT_TIME_SCALE),17280)
SLICE ?=
PY := uv run

.PHONY: help up down reset keys seed check verify test lint types demo demo-run demo-open sim sync eval eval-live record chaos mutation trace publish-check logs ps

help:
	@echo "make up | down | reset | demo | check | verify SLICE=S1 | sim | eval | chaos | logs"

keys:
	@CLOSEOUT_KEYS_DIR=.secrets $(PY) python -m closeout.keys

up: keys
	$(COMPOSE) build migrate
	$(COMPOSE) up -d --wait
	$(COMPOSE) run --rm -e CLOSEOUT_TIME_SCALE=$(SCALE) migrate python -m closeout.cli seed-if-empty
	@echo "Closeout is up: http://127.0.0.1:8000  (mail: http://127.0.0.1:8025, workflows: http://127.0.0.1:8233)"

down:
	$(COMPOSE) down

# Wipes everything: database, Temporal, Mailpit, uploads and keys. Then loads the seed.
reset:
	$(COMPOSE) down -v --remove-orphans
	rm -rf .secrets
	CLOSEOUT_KEYS_DIR=.secrets $(PY) python -m closeout.keys
	$(COMPOSE) build migrate
	$(COMPOSE) up -d --wait
	$(COMPOSE) run --rm -e CLOSEOUT_TIME_SCALE=$(SCALE) migrate python -m closeout.cli seed --scale $(SCALE)

seed:
	$(COMPOSE) run --rm migrate python -m closeout.cli seed --scale $(SCALE)

logs:
	$(COMPOSE) logs --tail=100

ps:
	$(COMPOSE) ps

lint:
	$(PY) ruff check src tests tools data evals
	$(PY) ruff format --check src tests tools data evals

types:
	$(PY) pyright

# All fast checks: lint, types, the architecture and config tests, and every test that needs no full stack.
check: lint types publish-check
	rm -rf build/trace
	$(PY) pytest -q -m "not stack and not live and not slow"

test:
	rm -rf build/trace
	$(PY) pytest -q -m "not stack and not live"
	$(PY) pytest -q -m "stack and not live"

# One slice's acceptance tests (needs `make up` for tests marked stack).
verify:
	@test -n "$(SLICE)" || (echo "usage: make verify SLICE=S1" && exit 2)
	rm -rf build/trace
	$(PY) pytest -q --slice $(SLICE) -m "not stack and not live"
	$(PY) pytest -q --slice $(SLICE) -m "stack and not live"
	$(PY) python tools/trace.py --upto $(SLICE)

trace:
	$(PY) python tools/trace.py

publish-check:
	$(PY) python tools/publish_check.py

mutation:
	$(PY) python tools/mutation_gate.py

demo: demo-run demo-open

demo-run:
	@echo "make demo arrives in S14"

demo-open:
	@command -v open >/dev/null && open http://127.0.0.1:8000 || true

# Run the permit feed once now (fetches the whole town from data.nj.gov).
sync:
	$(COMPOSE) exec -T temporal temporal schedule trigger --schedule-id permit-sync-1329 --address 127.0.0.1:7233

# Reading and attack evals in replay mode (what CI runs).
eval:
	$(PY) python -m evals.run_facts regression development
	$(PY) python -m evals.run_attacks

# Live models on this computer (needs Ollama with the two readers from evals/models.lock).
eval-live:
	$(PY) python -m evals.run_facts --live regression development sealed
	$(PY) python -m evals.run_attacks --live

# Read every eval document live and save replay files (owner's Mac; reviewed PR only).
record:
	$(PY) python -m evals.record regression development attacks messy town_replies
