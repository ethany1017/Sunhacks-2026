# KPHX airport simulator: backend targets (Python 3.9+)
PY := .venv/bin/python

.PHONY: venv graph scenarios sim busy api test agent-test web web-install demo

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

graph:
	$(PY) -m sim.graph.build_graph

scenarios:
	$(PY) -m sim.scenarios.generate

sim:
	$(PY) -m sim.core.run sim/scenarios/kphx_smoke.json

busy:
	$(PY) -m sim.core.run sim/scenarios/kphx_busy.json

api:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000

test:
	$(PY) -m pytest -q tests

# optional Claude agent (agent/), not part of the simulator
agent-test:
	.venv/bin/pip install -q -r agent/requirements.txt
	$(PY) -m pytest -q agent/tests

web-install:
	cd web && npm install

web:
	cd web && npm run dev

# API on :8000 and dashboard on :5173 together (Ctrl-C stops both)
demo:
	@trap 'kill 0' INT TERM; $(MAKE) api & (cd web && npm run dev) & wait
