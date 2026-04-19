.PHONY: help install dev up down build test integration-test deploy rollback logs clean

help:
	@echo "ChatOps DevOps - Developer Commands"
	@echo "  make install           - install all dependencies (python + node)"
	@echo "  make up                - start local dev stack via docker-compose"
	@echo "  make down              - stop local dev stack"
	@echo "  make build             - build all docker images"
	@echo "  make test              - run orchestrator unit tests"
	@echo "  make integration-test  - run integration tests (requires ENVIRONMENT=...)"
	@echo "  make deploy            - deploy to k8s (staging)"
	@echo "  make rollback          - rollback staging deployment"
	@echo "  make logs              - tail orchestrator logs"

install:
	cd orchestrator && pip install -r requirements.txt
	cd slack-gateway && npm install

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

test:
	cd orchestrator && pytest tests/ -v --tb=short

integration-test:
	@echo "Running integration tests against $(ENVIRONMENT)..."
	cd orchestrator && pytest tests/integration/ -v --env=$(ENVIRONMENT)

deploy:
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/rbac.yaml
	kubectl apply -f k8s/configmaps/
	kubectl apply -f k8s/deployments/
	kubectl apply -f k8s/services/
	kubectl apply -f k8s/ingress/

rollback:
	kubectl rollout undo deployment/chatops-orchestrator -n devops-tools

logs:
	kubectl logs -f deployment/chatops-orchestrator -n devops-tools

clean:
	docker-compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} +
