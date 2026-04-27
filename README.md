<br>

```
    ___         __  __              ____             __
   /   | ___   / /_/ /_  ___  ____/ __ \___  ____  / /___  __  __
  / /| |/ _ \ / __/ __ \/ _ \/ __/ / / / _ \/ __ \/ / __ \/ / / /
 / ___ /  __// /_/ / / /  __/ / / /_/ /  __/ /_/ / / /_/ / /_/ /
/_/  |_\___/ \__/_/ /_/\___/_/  \____/\___/ .___/_/\____/\__, /
                                          /_/            /____/
```

**Deploy anything, anywhere — just describe it.**

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-orange?style=flat-square)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

AetherDeploy is an AI agent that reads your codebase, proposes an optimal cloud architecture, and deploys it — all from a single natural-language instruction. No YAML to write, no cloud console to navigate.

```bash
aetherdeploy deploy "deploy my FastAPI app to production on AWS"
```

That's it. The agent handles the rest.

---

## How it works

AetherDeploy runs a graph of specialised nodes that take your project from source code to live infrastructure:

```
 Your instruction
       │
  ① Discovery          ── finds your project (local dir or GitHub URL)
       │
  ② Analysis           ── detects stack, frameworks, services, complexity
       │
  ③ Proposal           ── builds optimal architecture (deterministic + AI cost pass)
       │
  ④ ◉ Human review     ── you approve or tweak the plan (Human-in-the-Loop)
       │
  ⑤ Generation         ── writes Terraform + Docker Compose files
       │
  ⑥ Execution          ── runs terraform init / plan / apply (or docker compose up)
       │
  ⑦ ◉ Promote?         ── optionally promote from LocalStack → real cloud
       │
  ⑧ Done               ── live endpoints returned to you
```

Two **Human-in-the-Loop** checkpoints (`◉`) ensure you always approve before infrastructure changes happen.

---

## Features

| | |
|---|---|
| **Natural language** | English or Spanish — slash commands optional |
| **Multi-cloud** | AWS · GCP · Azure |
| **Three environments** | `local` (Docker Compose) · `feature` (LocalStack) · `prod` (real cloud) |
| **AI cost optimization** | LLM replaces each service with the cheapest valid alternative from a curated catalog |
| **Stack auto-detection** | Python · Node.js · Go · Java · Dockerfile |
| **IaC generation** | Terraform templates per cloud tier (nano → enterprise) |
| **GitHub support** | Paste a repo URL when no local project is found |
| **Interactive TUI** | Beautiful terminal UI built with Ink |
| **Programmatic SDK** | Embed in your own tools with a clean Python API |
| **OpenTelemetry** | Full tracing support with Langfuse / OTLP backends |
| **Offline-first LLM** | Works with local Ollama models; Anthropic Claude supported |

---

## Quick start

### 1 — Install

```bash
# Core (no cloud SDKs)
pip install aetherdeploy

# With AWS support
pip install "aetherdeploy[aws]"

# With everything
pip install "aetherdeploy[all]"
```

> **Requires:** Python 3.11+, Terraform CLI, Docker

### 2 — Configure

```bash
cp .env.example .env
```

```ini
# .env

# LLM backend: "ollama" (free, local) or "anthropic" (Claude API)
AETHER_LLM_BACKEND=ollama
AETHER_LLM_MODEL=gemma3:12b

# Cloud provider defaults
AETHER_DEFAULT_PROVIDER=aws
AETHER_DEFAULT_REGION=us-east-1
```

### 3 — Deploy

```bash
# From your project directory
aetherdeploy deploy "deploy this to prod"

# Or point to a specific path / environment
aetherdeploy deploy "ship to feature env on AWS" --project ./my-app --env feature

# Preview what would happen without applying
aetherdeploy deploy "plan prod deployment" --dry-run
```

---

## Interactive TUI

Launch the full terminal interface:

```bash
npx aetherdeploy
# or, after building the UI
node cli-ui/bin/aetherdeploy.js
```

You get a chat-like interface with live progress, proposal panels, and credential prompts — no browser needed.

```
┌─ AetherDeploy ──────────────────────────────────────────────────────────────┐
│                                                                              │
│  ✓ Stack detected: Python + FastAPI (profile: SMALL)                         │
│                                                                              │
│  ┌─ Architecture Proposal ─────────────────────────────────────────────────┐│
│  │  Provider: AWS  ·  Region: us-east-1  ·  Est. cost: ~$35-85/month       ││
│  │                                                                          ││
│  │  compute    Lambda + API Gateway          ~$0-50    serverless API       ││
│  │  database   DynamoDB On-Demand            ~$0-25    no schema needed     ││
│  │  networking API Gateway HTTP API          ~$0-15    low-overhead routing ││
│  └──────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  Proceed with this proposal? [Y/n]  _                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Slash commands

| Command | Description |
|---------|-------------|
| `/init` | Analyse the project and generate a proposal — no deployment |
| `/plan` | Generate Terraform and run `terraform plan` without applying |
| `/deploy` | Apply the proposal to the selected environment |
| `/destroy` | Tear down all infrastructure for this project |
| `/status` | Show the current agent step |
| `/help` | List available commands |

All commands also work as natural language — the agent understands both.

---

## Environments

| Environment | What happens |
|-------------|--------------|
| `local` | Docker Compose spun up on your machine |
| `feature` | App + LocalStack in Docker; Terraform applied against `localhost:4566` |
| `prod` | Real cloud infrastructure provisioned with Terraform |

### Feature → Prod promotion

After a successful `feature` deployment you are asked:

```
✓ Your app is running in the feature environment (LocalStack).
  Endpoints: http://localhost:8080, http://localhost

Promote to real production? [y/N]
```

A second HIL checkpoint keeps accidental production deployments from happening.

---

## AI cost optimization

The proposal engine runs two passes:

**Pass 1 — Deterministic baseline**  
Service selection matrices pick a default service for each purpose (compute, database, cache, …) based on detected project signals. No LLM needed — instant.

**Pass 2 — LLM optimization** *(single API call)*  
The LLM receives the baseline proposal and the full `SERVICE_ALTERNATIVES` catalog with cost bounds and compatibility rules. It replaces each service with the cheapest valid option, then returns security notes and a rationale.

```
ECS Fargate ($80-300) → Lambda + API Gateway ($0-50)   saves ~$80/month
Aurora Serverless   → DynamoDB On-Demand ($0-25)       saves ~$150/month

Total: ~$35-85/month  (was ~$230-500)
```

If the LLM is unavailable, the baseline proposal is used unchanged — the agent never blocks.

See [`docs/ai-cost-optimization.md`](docs/ai-cost-optimization.md) for the full catalog structure and how to add alternatives.

---

## Python SDK

```python
from aetherdeploy import AetherDeploy

agent = AetherDeploy(provider="aws")

# Basic deploy
result = await agent.deploy(
    project="./my-app",
    instruction="deploy to production",
    environments=["prod"],
)
print(result.endpoints)   # ['https://abc123.execute-api.us-east-1.amazonaws.com']

# With Human-in-the-Loop callback
async def on_proposal(proposal) -> bool:
    print(f"Estimated cost: {proposal.total_estimated_cost}")
    return True  # approve automatically

result = await agent.deploy(
    project="./my-app",
    instruction="deploy to feature and prod",
    environments=["feature", "prod"],
    on_proposal=on_proposal,
)

# Analyse only (no deployment)
analysis = await agent.analyze(project="./my-app")
print(analysis.primary_language, analysis.architecture)
```

---

## Project structure

```
src/aetherdeploy/
├── agent/
│   ├── graph.py            # LangGraph state machine
│   ├── context.py          # Shared runtime context (_emitter_var)
│   ├── nodes/
│   │   ├── discovery.py    # Project detection
│   │   ├── analysis.py     # Static + LLM stack analysis
│   │   ├── proposal.py     # Architecture proposal + cost optimization
│   │   ├── confirmation.py # HIL checkpoint #1
│   │   ├── generation.py   # Terraform / Docker Compose generation
│   │   ├── execution.py    # terraform apply / docker compose up
│   │   └── promotion.py    # HIL checkpoint #2 (feature → prod)
│   └── tools/              # Shell, filesystem, GitHub helpers
├── analyzers/
│   ├── detector.py         # Multi-language topology detector
│   ├── llm_enricher.py     # LLM second pass for ambiguous projects
│   ├── python.py           # Python / FastAPI / Django analyzer
│   ├── node.py             # Node.js / Next.js / Express analyzer
│   ├── go.py               # Go analyzer
│   └── java.py             # Java / Spring analyzer
├── providers/
│   ├── aws/                # Service matrices + alternatives catalog
│   ├── gcp/                # GCP service matrices
│   └── azure/              # Azure service matrices
├── terraform/
│   ├── generator.py        # Jinja2 template renderer
│   ├── runner.py           # terraform CLI wrapper (streaming)
│   └── templates/          # Per-provider, per-tier .tf.j2 templates
├── llm/
│   ├── anthropic.py        # Claude backend (with prompt caching)
│   └── ollama.py           # Ollama backend (local models)
├── cli.py                  # Typer CLI + stream mode
├── cli_nlu.py              # Natural language intent detection
├── cli_credentials.py      # Credential validation (AWS / GCP / Azure)
├── config.py               # Pydantic settings
├── models.py               # Shared dataclasses
├── observability.py        # Structured logging + OpenTelemetry
└── sdk.py                  # Public Python SDK
```

---

## Configuration reference

All settings are prefixed with `AETHER_` and can be set in `.env` or as environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `AETHER_LLM_BACKEND` | `ollama` | LLM provider: `ollama` or `anthropic` |
| `AETHER_LLM_MODEL` | `gemma3:1b` | Model name |
| `AETHER_LLM_BASE_URL` | `http://localhost:11434` | Ollama base URL |
| `AETHER_LLM_API_KEY` | — | Anthropic API key |
| `AETHER_LLM_WAIT_FOREVER` | `false` | Retry LLM indefinitely when unavailable |
| `AETHER_DEFAULT_PROVIDER` | `aws` | Cloud provider: `aws`, `gcp`, `azure` |
| `AETHER_DEFAULT_REGION` | `us-east-1` | Default deployment region |
| `AETHER_TERRAFORM_BINARY` | `terraform` | Path to the Terraform binary |
| `AETHER_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `AETHER_OTEL_ENDPOINT` | `http://localhost:4317` | OTLP gRPC endpoint |

---

## Optional dependencies

Install only what you need:

```bash
pip install "aetherdeploy[aws]"    # boto3 — AWS credential verification
pip install "aetherdeploy[gcp]"    # google-auth — GCP credential verification
pip install "aetherdeploy[azure]"  # azure-identity — Azure credential verification
pip install "aetherdeploy[otel]"   # opentelemetry-sdk + exporter
pip install "aetherdeploy[all]"    # everything
```

---

## Development

```bash
# Clone and install in editable mode
git clone https://github.com/your-org/aether-deploy
cd aether-deploy
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Type-check
mypy src/

# Build the TUI
cd cli-ui
npm install
npm run build
```

### Supported stacks

The static analyzer auto-detects:

| Language | Frameworks detected |
|----------|---------------------|
| **Python** | FastAPI, Flask, Django, Celery |
| **Node.js** | Next.js, Express, NestJS, React |
| **Go** | net/http, Gin, Echo |
| **Java** | Spring Boot, Maven, Gradle |
| Any | Dockerfile, Docker Compose |

### Adding a cloud provider alternative

1. Add a `ServiceOption` entry to `providers/aws/services.py` (keep sorted cheapest-first)
2. Ensure the `resource` value is handled in a Jinja2 template
3. Set `requires` / `excludes` constraints
4. Run `aetherdeploy analyze --llm` to validate signal detection

---

## Observability

AetherDeploy writes structured JSON logs to `.aetherdeploy/logs/agent.jsonl`:

```jsonc
{"ts":"2026-04-27T10:23:01+0000","level":"INFO","event":"agent.node.end",
 "node":"analysis_node","duration_ms":312,"profile":"small","llm_used":true}
```

Enable OpenTelemetry to forward spans to Langfuse, Jaeger, or any OTLP-compatible backend:

```ini
AETHER_OTEL_ENABLED=true
AETHER_OTEL_ENDPOINT=https://api.langfuse.com/otlp/v1/traces
```

---

## Roadmap

- [ ] GCP cost optimization catalog
- [ ] Azure cost optimization catalog
- [ ] Pulumi backend (alongside Terraform)
- [ ] Multi-region deployments
- [ ] Drift detection (`/check` command)
- [ ] Web UI (Next.js)

---

## License

MIT © AetherDeploy contributors
