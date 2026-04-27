# AI Cost Optimization in AetherDeploy

AetherDeploy uses a two-pass system to select cloud services: a deterministic
baseline that runs instantly, followed by an AI pass that replaces each service
with the cheapest valid alternative.

---

## How it works

```
User instruction
      │
      ▼
┌─────────────────────────────┐
│  Pass 1 — Matrix baseline   │  (instant, no LLM)
│  provider.recommend_arch()  │
│  → default services         │
└─────────────┬───────────────┘
              │
              ▼
┌──────────────────────────────────────────┐
│  Pass 2 — LLM optimization + review      │  (single LLM call)
│                                          │
│  Input:                                  │
│    • baseline services                   │
│    • SERVICE_ALTERNATIVES catalog        │
│    • project hints (has-database, etc.)  │
│                                          │
│  LLM output:                             │
│    • service_overrides (cheaper picks)   │
│    • security_notes                      │
│    • scalability_notes                   │
│    • rationale                           │
│                                          │
│  _apply_service_overrides()              │
│    → patches proposal in-place           │
│    → recalculates total_estimated_cost   │
└──────────────────────────────────────────┘
              │
              ▼
    Final ArchitectureProposal
    (shown in ProposalPanel)
```

If the LLM is unavailable the baseline proposal is used unchanged. The agent
never blocks on LLM availability — `llm_wait_forever` controls retry behaviour.

---

## Selection algorithm

For each service purpose the LLM applies:

```python
valid_options = [
    option
    for option in catalog[purpose]
    if not any(h in project_hints for h in option["excludes"])
    and (
        not option["requires"]
        or any(h in project_hints for h in option["requires"])
    )
]
chosen = min(valid_options, key=lambda o: o["cost_high"])
```

Rules in plain English:
- **excludes**: if any listed hint is detected → skip this option.
- **requires**: if the list is non-empty and none of the listed hints are
  detected → skip this option.
- Among valid options, pick the one with the lowest `cost_high`.

An override is only emitted when the chosen service differs from the matrix
default. If the matrix already picked the cheapest valid option, no override
is emitted and the proposal is unchanged.

---

## Service alternatives catalog (`SERVICE_ALTERNATIVES`)

Defined in `src/aetherdeploy/providers/aws/services.py`.

### Structure

```python
SERVICE_ALTERNATIVES: dict[str, list[ServiceOption]] = {
    "<purpose>": [
        {
            "service":       str,   # Display name, e.g. "Lambda + API Gateway"
            "resource":      str,   # Terraform resource, e.g. "aws_lambda_function"
            "justification": str,   # Why this option is best (shown to user)
            "cost_low":      int,   # Min USD/month
            "cost_high":     int,   # Max USD/month (used for ranking)
            "requires":      list[str],  # Hints that MUST be present
            "excludes":      list[str],  # Hints that FORBID this option
        },
        # ... more options, ordered cheapest-first
    ],
    # ...
}
```

### Current purposes and options

| Purpose | Cheapest | Most expensive |
|---------|----------|----------------|
| `compute` | Lambda + API Gateway ($0-50) | EKS Fargate ($150-500) |
| `database` | DynamoDB On-Demand ($0-25) | RDS Aurora Serverless v2 ($25-200) |
| `cache` | ElastiCache Serverless ($0-60) | ElastiCache t4g.micro ($12-80) |
| `networking` | API Gateway HTTP API ($0-15) | VPC + ALB ($16-40) |
| `storage` | S3 Standard ($0-20) | S3 Standard ($0-20) |
| `queue` | EventBridge ($0-15) | SQS ($0-20) |
| `security` | WAF + Shield Standard ($20-60) | WAF + Shield Standard ($20-60) |

### Key constraint examples

**Lambda** (`excludes: ["has-worker", "has-websocket", "fullstack"]`):
Projects with background workers, WebSocket connections, or full-stack apps
cannot use Lambda's 15-minute execution limit and stateless model.

**DynamoDB** (`excludes: ["has-relational-schema"]`):
Projects that use SQL JOIN queries, foreign keys, or ORM migrations with
relational schemas cannot be served by DynamoDB without application rewrites.

**EKS Fargate** (`requires: ["multi-service", "has-service-discovery"]`):
Kubernetes is only proposed when the project already relies on service
discovery primitives — otherwise ECS Fargate is simpler and cheaper.

---

## LLM prompt design

### System prompt (`COST_OPTIMIZE_SYSTEM`)

The prompt instructs the LLM to:
1. Apply the selection algorithm explicitly (included as pseudocode).
2. Return `service_overrides` only for services that change.
3. Include `security_notes`, `scalability_notes`, and `rationale` in the
   same JSON response (single round-trip).
4. Never hallucinate services outside the provided catalog.

Output schema:
```json
{
  "service_overrides": [
    {
      "purpose": "compute",
      "service": "Lambda + API Gateway",
      "resource": "aws_lambda_function",
      "justification": "Low traffic API; Lambda saves ~$70/month vs ECS",
      "cost_range": "~$0-50",
      "saving_vs_default": "saves ~$70/month vs ECS Fargate"
    }
  ],
  "rationale": "...",
  "security_notes": ["..."],
  "scalability_notes": ["..."]
}
```

### Provider coverage

| Provider | Cost optimization | Architecture review |
|----------|:-----------------:|:-------------------:|
| AWS | ✓ (full catalog) | ✓ |
| GCP | — (no catalog yet) | ✓ (legacy prompt) |
| Azure | — (no catalog yet) | ✓ (legacy prompt) |

GCP and Azure use the legacy `ARCHITECTURE_REVIEW_SYSTEM` prompt which adds
notes but does not change service selection.

---

## How overrides are applied (`_apply_service_overrides`)

```
For each override emitted by the LLM:
  1. Look up the ServiceRecommendation with matching purpose.
  2. If purpose not found → log warning, skip (prevents phantom services).
  3. If service name unchanged → skip (LLM confirmed matrix was optimal).
  4. Patch in-place:
       service_name          ← override.service
       terraform_resource    ← override.resource
       justification         ← override.justification
       estimated_monthly_cost ← override.cost_range
  5. Record change for the optimization summary shown in the chat.

After all overrides:
  _recalculate_total_cost() sums low/high bounds and updates
  proposal.total_estimated_cost.
```

---

## Adding a new alternative

1. **Add the `ServiceOption` entry** to the relevant list in
   `src/aetherdeploy/providers/aws/services.py`:
   ```python
   {
       "service": "My New Service",
       "resource": "aws_my_resource",
       "justification": "Best for X workloads because Y",
       "cost_low": 10,
       "cost_high": 50,
       "requires": ["has-feature-x"],
       "excludes": ["has-incompatible-thing"],
   },
   ```
   Keep the list **sorted cheapest-first** (`cost_high` ascending) so the
   LLM's natural fallback is always the cheapest valid option.

2. **Ensure template support**: the `resource` value must appear in a
   `compute_resource` check in at least one Jinja2 template, or the
   Terraform generator will produce an empty compute section.

3. **Test the constraints**: run the project through `aetherdeploy analyze`
   to see which hints are detected, then verify your `requires`/`excludes`
   rules would produce the expected selection.

---

## Adding a catalog for GCP or Azure

1. Create `src/aetherdeploy/providers/gcp/services.py` (or `azure/`) with a
   `SERVICE_ALTERNATIVES` dict using the same `ServiceOption` shape.
2. In `proposal.py`, extend the `use_cost_optimize` check:
   ```python
   use_cost_optimize = provider_name in ("aws", "gcp")
   catalog = SERVICE_ALTERNATIVES  # import from the correct module
   ```
3. Add the new prompt variables to `cost_optimize_user_prompt` if the
   provider has provider-specific signals.

---

## Cost recalculation

`_recalculate_total_cost` uses a simple regex to parse cost strings:

```
~$low-high  →  adds low to low_total, high to high_total
```

Any service whose cost string doesn't match is treated as $0 (conservative).
The result is written back as `~$low_total-high_total/month`.

For accuracy, keep `estimated_monthly_cost` in the `"~$N-M"` format for all
`ServiceOption` entries in the catalog.
