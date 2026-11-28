# Closeout demo. Everything runs on this computer (ports on 127.0.0.1 only).
-include .env
COMPOSE := docker compose -f deploy/compose.yaml
SCALE ?= $(or $(CLOSEOUT_TIME_SCALE),17280)
SLICE ?=
PY := uv run

.PHONY: help image up down reset keys seed check verify test lint types demo demo-run demo-open sim sync eval eval-live record chaos mutation trace publish-check logs ps

help:
	@echo "make up | down | reset | demo | check | verify SLICE=S1 | sim | eval | chaos | logs"

image:
	$(COMPOSE) build migrate || (sleep 3 && $(COMPOSE) build migrate)

# Keys are made inside the app image, so a fresh clone needs only Docker and make.
keys: image
	@mkdir -p .secrets
	@docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/.secrets:/out" -e CLOSEOUT_KEYS_DIR=/out \
		closeout-app:local python -m closeout.keys

up: keys
	$(COMPOSE) up -d --wait
	$(COMPOSE) run --rm -e CLOSEOUT_TIME_SCALE=$(SCALE) migrate python -m closeout.cli seed-if-empty
	@echo "Closeout is up: http://127.0.0.1:8000  (mail: http://127.0.0.1:8025, workflows: http://127.0.0.1:8233)"

down:
	$(COMPOSE) down

# Wipes everything: database, Temporal, Mailpit, uploads and keys. Then loads the seed.
reset:
	$(COMPOSE) down -v --remove-orphans
	rm -rf .secrets
	$(MAKE) keys
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

# Every simulated person plays their row of the spec, then every end state is checked (fresh start).
# The simulation runs at 1 day = 10 s, so the bots answer in time even on a slow or busy computer.
SIM_SCALE ?= 8640
sim:
	$(MAKE) reset SCALE=$(SIM_SCALE)
	$(COMPOSE) run --rm sims

demo: demo-run demo-open

# Plays every role in the spec's persona table (H1-H12, T1-T3, K1) through the real pages and links, then
# prints the pass/fail list and the end-state summary. Replay mode: no AI downloads, no keys to fetch.
demo-run:
	$(MAKE) reset SCALE=$(SIM_SCALE)
	@mkdir -p build
	@$(COMPOSE) run --rm sims > build/demo-report.txt 2>&1; s=$$?; cat build/demo-report.txt; exit $$s

demo-open:
	@echo "Open http://127.0.0.1:8000 (pick a person). Mail: http://127.0.0.1:8025"
	@[ -n "$$CI" ] || { command -v open >/dev/null && open http://127.0.0.1:8000; } || true

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
