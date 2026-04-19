# ChatOps DevOps Bot

Slack-driven ChatOps platform that bridges Slack with Jenkins, Docker, and Kubernetes.
Architecture and design rationale: see `ChatOps_DevOps_RnD.md`.

## Layout

```
slack-gateway/   Node.js Bolt.js Slack receiver
orchestrator/    Python FastAPI core (parser, adapters, audit, metrics)
k8s/             Kubernetes manifests (RBAC, deployments, services, ingress)
jenkins/         Bot's own CI/CD pipeline (Jenkinsfile)
schema/          PostgreSQL schema (auto-loaded by the docker-compose postgres)
config/          Runtime config (permissions.yaml)
docker-compose.yml
Makefile
```

## Local development

1. Copy env template: `cp .env.template .env` and fill in Slack/Jenkins tokens.
2. `make install`
3. `make up` — starts postgres, redis, orchestrator, slack-gateway, adminer.
4. Orchestrator: http://localhost:8000 (health at `/health`, metrics at `/metrics`).
5. Adminer (DB UI): http://localhost:8080.

Assign a Slack user a role so they can execute commands:

```sql
INSERT INTO user_roles (slack_user_id, display_name, email, roles)
VALUES ('U01ABCDEF', 'Alice', 'alice@example.com', ARRAY['devops-lead']);
```

## Deploying to Kubernetes

```
cp k8s/secrets/secrets.yaml.template k8s/secrets/secrets.yaml   # fill in values
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets/secrets.yaml
kubectl apply -f k8s/configmaps/
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/deployments/
kubectl apply -f k8s/services/
kubectl apply -f k8s/ingress/
```

Or simply: `make deploy`.

## Slash commands supported

`/deploy` `/build` `/status` `/rollback` `/logs` `/restart` `/scale`

Register these in your Slack App with request URL `https://<host>/slack/events` (or use Socket Mode locally via `SLACK_APP_TOKEN`).
