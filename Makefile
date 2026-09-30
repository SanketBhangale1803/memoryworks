SHELL := /bin/bash
BACKEND_PY := backend/.venv/bin/python
MCP_PY := mcp_server/.venv/bin/python

.PHONY: memoryworks orgmemory runbook dev backend frontend mcp mcp-http test sdk-test sdk-install lint ci conference-check runtime-check format demo reset docker-up docker-down arcade-init graph-check benchmark

memoryworks:
	docker compose --profile mcp up --build

# Aliases for the product's earlier names, so old instructions still work.
orgmemory: memoryworks
runbook: memoryworks

dev: docker-up

docker-up:
	docker compose --profile mcp up --build -d

docker-down:
	docker compose --profile mcp down

$(BACKEND_PY):
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt

$(MCP_PY):
	python3 -m venv mcp_server/.venv
	mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt

frontend/node_modules:
	cd frontend && npm install

backend: $(BACKEND_PY)
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000

frontend: frontend/node_modules
	cd frontend && npm run dev

mcp: $(MCP_PY)
	MEMORYWORKS_API_URL="$${MEMORYWORKS_API_URL:-$${ORGMEMORY_API_URL:-$${RUNBOOK_API_URL:-http://localhost:8000}}}" MEMORYWORKS_API_KEY="$${MEMORYWORKS_API_KEY:-$${ORGMEMORY_API_KEY:-$${RUNBOOK_API_KEY:-}}}" $(MCP_PY) mcp_server/server.py --transport stdio

mcp-http: $(MCP_PY)
	MEMORYWORKS_API_URL="$${MEMORYWORKS_API_URL:-$${ORGMEMORY_API_URL:-$${RUNBOOK_API_URL:-http://localhost:8000}}}" MEMORYWORKS_MCP_PUBLIC_URL="$${MEMORYWORKS_MCP_PUBLIC_URL:-$${ORGMEMORY_MCP_PUBLIC_URL:-$${MCP_PUBLIC_URL:-http://localhost:8001}}}" MEMORYWORKS_MCP_OAUTH_ISSUER_URL="$${MEMORYWORKS_MCP_OAUTH_ISSUER_URL:-$${ORGMEMORY_MCP_OAUTH_ISSUER_URL:-$${MCP_OAUTH_ISSUER_URL:-http://localhost:8000}}}" $(MCP_PY) mcp_server/server.py --transport streamable-http --host 0.0.0.0 --port 8001

test: $(BACKEND_PY) frontend/node_modules
	$(BACKEND_PY) -m pytest backend
	cd frontend && npm test
	PYTHONPATH=python_sdk/src $(BACKEND_PY) -m pytest -q python_sdk/tests

sdk-test: $(BACKEND_PY)
	PYTHONPATH=python_sdk/src $(BACKEND_PY) -m pytest -q python_sdk/tests

sdk-install: $(BACKEND_PY)
	$(BACKEND_PY) -m pip install -e ./python_sdk

lint: $(BACKEND_PY) frontend/node_modules
	$(BACKEND_PY) -m ruff check backend/app backend/tests backend/scripts
	$(BACKEND_PY) -m black --check backend/app backend/tests backend/scripts
	cd frontend && npx tsc --noEmit

ci: test lint
	cd frontend && npm run build

conference-check: ci
	docker compose config --quiet

runtime-check:
	docker compose exec backend curl --fail --silent --show-error http://localhost:8000/api/health
	docker compose exec backend curl --fail --silent --show-error http://localhost:8000/api/health/graph
	docker compose exec backend curl --fail --silent --show-error --head http://frontend:3000

format: $(BACKEND_PY)
	$(BACKEND_PY) -m ruff check --fix backend/app backend/tests backend/scripts
	$(BACKEND_PY) -m black backend/app backend/tests backend/scripts

arcade-init:
	docker compose up -d arcadedb
	docker compose run --rm backend python scripts/init_arcade.py

graph-check:
	docker compose exec backend python scripts/graph_check.py

demo:
	docker compose exec backend python scripts/load_demo.py

reset:
	docker compose exec backend python scripts/reset.py

benchmark:
	$(MAKE) -C ../hcag benchmark
