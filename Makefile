.PHONY: help build up down logs test clean lint

help:
	@echo "Alarm Investigation Copilot - Available commands:"
	@echo ""
	@echo "  make build        - Build Docker images"
	@echo "  make up           - Start all services with docker compose"
	@echo "  make down         - Stop all services"
	@echo "  make logs         - View logs from all services"
	@echo "  make logs-backend - View copilot backend logs"
	@echo "  make logs-api     - View alarm simulator logs"
	@echo "  make test         - Run all tests"
	@echo "  make test-e2e     - Run E2E tests"
	@echo "  make lint         - Run code linting"
	@echo "  make clean        - Remove containers and volumes"
	@echo "  make ingest       - Ingest RAG documents"
	@echo "  make status       - Check service status"

build:
	docker compose build

up:
	docker compose up -d
	@echo "Services starting..."
	@sleep 5
	@make status

down:
	docker compose down

logs:
	docker compose logs -f

logs-backend:
	docker compose logs -f copilot-backend

logs-api:
	docker compose logs -f alarm-simulator

logs-mcp:
	docker compose logs -f alarm-mcp

test:
	pytest tests/ -v --cov=. --tb=short

test-e2e:
	pytest tests/e2e/ -v

test-unit:
	pytest tests/unit/ -v

lint:
	black apps/ mcp-servers/ rag/ tests/
	isort apps/ mcp-servers/ rag/ tests/
	flake8 apps/ mcp-servers/ rag/ tests/ --max-line-length=100

clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage

status:
	@echo "Checking service status..."
	@curl -s http://localhost:8000/health | jq . || echo "❌ Alarm Simulator: Not responding"
	@curl -s http://localhost:8001/health | jq . || echo "❌ Copilot Backend: Not responding"
	@echo "Frontend: http://localhost:3000"

ingest:
	@docker compose stop copilot-backend
	@docker compose run --rm --no-deps copilot-backend python -m rag.ingestion.ingester; result=$$?; docker compose start copilot-backend; exit $$result

dev:
	@echo "Starting development environment..."
	@echo "Run these in separate terminals:"
	@echo "  Terminal 1: python apps/backend/simulator.py"
	@echo "  Terminal 2: python mcp-servers/alarm-management/server.py"
	@echo "  Terminal 3: python apps/backend/copilot.py"

docs:
	@echo "Opening documentation..."
	@open README.md || cat README.md

demo:
	@echo "Running demo scenario..."
	@curl -X POST http://localhost:8001/investigate \
		-H "Content-Type: application/json" \
		-d '{"query": "Show active critical alarms for Boiler Feed Pump 101", "use_rag": true}' | jq .

shell-backend:
	docker compose exec copilot-backend /bin/bash

shell-simulator:
	docker compose exec alarm-simulator /bin/bash
