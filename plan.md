# Plan de Implementación — AetherDeploy

## Contexto

AetherDeploy es un sistema agéntico de despliegue multicloud que permite a usuarios desplegar cualquier proyecto de software en GCP, Azure y AWS usando lenguaje natural. El proyecto parte de fase cero: solo existe `prompt.md` con la especificación. El plan implementa el sistema de forma incremental, donde cada fase entrega algo funcional y verificable.

---

## Arquitectura Global: Dos Capas

```
┌─────────────────────────────────────────────┐
│  CLI UI Layer — Node.js + Ink (React)       │  ← plan-cli-ui.md
│  Mascota animada · Layout Copilot · Input   │
└────────────┬────────────────────────────────┘
             │  JSON-lines sobre stdio (IPC)
┌────────────▼────────────────────────────────┐
│  Agent Backend — Python + LangGraph         │  ← este plan
│  LLM · Análisis · IaC · Despliegue         │
└─────────────────────────────────────────────┘
```

El frontend Ink spawna el backend Python con `--stream-json`. Python emite eventos JSON estructurados; Ink los renderiza con animaciones. Ver [plan-cli-ui.md](plan-cli-ui.md) para el detalle completo de la UI.

---

## Stack Tecnológico

### Backend (Python) — este plan

| Componente | Tecnología | Razón |
|---|---|---|
| Lenguaje | Python 3.11+ | Ecosistema IA, SDKs cloud maduros |
| Agente / Grafo | LangGraph | HIL nativo via `interrupt_before`, estado persistente con ciclos |
| LLM (inicial) | Ollama + Gemma3 | Local, sin coste, sin API key — intercambiable vía interfaz limpia |
| LLM (interfaz) | `LLMBackend` (abstracción propia) | Permite cambiar a Anthropic/OpenAI/Mistral sin tocar nodos del agente |
| CLI (stream) | Typer + `--stream-json` | Emite eventos JSON-lines para el frontend Ink |
| IaC | Terraform + Jinja2 | Determinismo; el LLM decide QUÉ, templates generan el HCL |
| Contenedores (local) | Docker Compose | Despliegue local del proyecto con servicios auxiliares |
| Contenedores (feature) | LocalStack | Emula AWS localmente — mismos ficheros `.tf`, sin coste ni credenciales reales |
| Config | Pydantic v2 + python-dotenv | Validación de settings |
| Testing | pytest + pytest-asyncio + moto | Tests unitarios e integración |
| Observabilidad | OpenTelemetry | Trazas de ejecución del agente |
| Packaging | pyproject.toml + Hatch | `pip install aetherdeploy` |

### Frontend (Node.js) — ver plan-cli-ui.md

| Componente | Tecnología | Razón |
|---|---|---|
| CLI UI | Ink (React para terminal) | Componentes reactivos, animaciones con estado, nivel Copilot |
| Animaciones | frames + `setInterval` | Control por estado, cancelable, no molesto |
| Estilos | chalk + ink-gradient | Colores hex exactos, gradientes para branding |

**Por qué LocalStack para el entorno feature**: LocalStack emula la API de AWS completa en `localhost:4566`. Los mismos ficheros Terraform que se usarán en producción corren contra LocalStack sin credenciales reales ni costes. El usuario valida que su app funciona sobre la arquitectura cloud propuesta antes de comprometerse con un despliegue real. La decisión de "promover a prod" es un segundo HIL explícito.

**Por qué LangGraph**: Es un grafo dirigido con ciclos y pausas HIL nativas (`interrupt_before`). AetherDeploy es un único agente con herramientas siguiendo un flujo de estados — exactamente el caso de uso de LangGraph.

**Por qué Ollama + Gemma3**: Arranque sin fricción (sin API keys, sin coste), ejecuta localmente. La arquitectura desacopla completamente el LLM del resto del sistema mediante una interfaz `LLMBackend`, por lo que cambiar a Anthropic, OpenAI o cualquier otro proveedor es un cambio de configuración, no de código.

**Por qué Ink y no Rich (Python)**: Rich puede hacer paneles y spinners, pero no tiene el modelo de componentes reactivos de React. Para animaciones con estados complejos y layout Copilot-level, Ink es significativamente más potente. La separación permite que cada capa evolucione independientemente.

---

## Estructura de Directorios Objetivo

```
aether-deploy/
├── pyproject.toml
├── README.md
├── CLAUDE.md
├── .env.example
├── .gitignore
│
├── src/
│   └── aetherdeploy/
│       ├── __init__.py
│       ├── cli.py                    # Entry point CLI (Typer)
│       ├── sdk.py                    # Entry point SDK pública
│       ├── config.py                 # Settings con Pydantic BaseSettings
│       │
│       ├── llm/
│       │   ├── base.py               # LLMBackend (ABC) + LLMBackendFactory [CRÍTICO]
│       │   ├── ollama.py             # Backend inicial: Ollama + Gemma3
│       │   └── anthropic.py          # Backend alternativo (deshabilitado por defecto)
│       │
│       ├── agent/
│       │   ├── graph.py              # Grafo LangGraph + HIL via interrupt_before [CRÍTICO]
│       │   ├── state.py              # AetherState TypedDict (contrato central) [CRÍTICO]
│       │   ├── nodes/
│       │   │   ├── discovery.py      # Inspección de proyecto / clonado GitHub
│       │   │   ├── analysis.py       # Análisis inteligente con ProjectDetector
│       │   │   ├── proposal.py       # Propuesta de arquitectura cloud
│       │   │   ├── confirmation.py   # Nodo HIL — procesa respuesta del usuario
│       │   │   ├── generation.py     # Generación de ficheros Terraform
│       │   │   └── execution.py      # Ejecución del despliegue por entorno
│       │   └── tools/
│       │       ├── filesystem.py     # Lectura de proyecto
│       │       ├── github.py         # Clonado de repos
│       │       └── shell.py          # Ejecución de comandos
│       │
│       ├── analyzers/
│       │   ├── base.py               # Clase abstracta LanguageAnalyzer
│       │   ├── detector.py           # Orquesta detección [CRÍTICO]
│       │   ├── node.py               # Analizador Node.js/TypeScript
│       │   ├── python.py             # Analizador Python
│       │   ├── java.py               # Analizador Java/Kotlin
│       │   └── go.py                 # Analizador Go
│       │
│       ├── providers/
│       │   ├── base.py               # CloudProvider (ABC) + dataclasses [CRÍTICO]
│       │   ├── __init__.py           # ProviderRegistry (detección de credenciales)
│       │   ├── aws/
│       │   │   ├── provider.py       # AWSProvider con boto3
│       │   │   └── services.py       # COMPUTE_MATRIX: mapeo tipo-app → servicios AWS
│       │   ├── gcp/
│       │   │   ├── provider.py
│       │   │   └── services.py
│       │   └── azure/
│       │       ├── provider.py
│       │       └── services.py
│       │
│       ├── terraform/
│       │   ├── generator.py          # TerraformGenerator con Jinja2
│       │   ├── runner.py             # Wrapper del CLI terraform
│       │   └── templates/
│       │       ├── aws/main.tf.j2
│       │       ├── gcp/main.tf.j2
│       │       └── azure/main.tf.j2
│       │
│       ├── docker/
│       │   ├── composer.py           # Genera docker-compose.yml
│       │   └── runner.py             # Controla Docker via SDK
│       │
│       └── output/
│           └── renderer.py           # Renderizado con Rich (centralizado)
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_analyzers.py
│   │   ├── test_providers.py
│   │   └── test_terraform_generator.py
│   ├── integration/
│   │   ├── test_graph_flow.py
│   │   └── test_cli.py
│   └── fixtures/
│       ├── sample_node_project/      # package.json + Express + Dockerfile
│       ├── sample_python_project/    # pyproject.toml + FastAPI
│       └── sample_java_project/      # pom.xml + Spring Boot
│
└── docs/
    ├── architecture.md
    └── provider-mapping.md
```

---

## FASE 1: Fundamentos y Esqueleto del Proyecto ✅ COMPLETADA

**Objetivo**: Proyecto Python instalable con CLI funcional, grafo LangGraph vacío pero ejecutable, y abstracción LLM en su lugar.

**Criterio de verificación**: `aetherdeploy --help` funciona. `aetherdeploy "hola"` responde con el estado inicial. Tests básicos pasan.

### Ficheros a crear

#### `pyproject.toml`
- `[project.scripts]` → `aetherdeploy = "aetherdeploy.cli:app"`
- Dependencies: `langgraph>=0.2`, `typer[all]>=0.12`, `rich>=13`, `pydantic-settings>=2.5`, `python-dotenv>=1.0`, `gitpython>=3.1`, `httpx>=0.27`
- Dev deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`

#### `src/aetherdeploy/llm/base.py` — Abstracción LLM (CRÍTICO para intercambiabilidad)

```python
class LLMBackend(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], system: str = "") -> str: ...
    
    @abstractmethod
    async def complete_json(self, messages: list[dict], system: str = "") -> dict: ...

class LLMBackendFactory:
    @staticmethod
    def create(backend: str, **kwargs) -> LLMBackend:
        if backend == "ollama":
            return OllamaBackend(**kwargs)
        elif backend == "anthropic":
            return AnthropicBackend(**kwargs)
        raise ValueError(f"Backend desconocido: {backend}")
```

#### `src/aetherdeploy/llm/ollama.py` — Backend inicial

```python
class OllamaBackend(LLMBackend):
    def __init__(self, model: str = "gemma3", base_url: str = "http://localhost:11434"):
        self._model = model
        self._client = httpx.AsyncClient(base_url=base_url)
    
    async def complete(self, messages: list[dict], system: str = "") -> str:
        # POST /api/chat
    
    async def complete_json(self, messages: list[dict], system: str = "") -> dict:
        # format="json" en la petición
```

#### `src/aetherdeploy/config.py`

```python
class AetherConfig(BaseSettings):
    llm_backend: str = "ollama"
    llm_model: str = "gemma3"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str | None = None
    
    default_provider: Literal["aws", "gcp", "azure"] = "aws"
    default_region: str = "us-east-1"
    terraform_binary: str = "terraform"
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AETHER_")
```

#### `src/aetherdeploy/agent/state.py` — Contrato central (CRÍTICO)

```python
class AetherState(TypedDict):
    user_message: str
    project_path: str | None
    github_url: str | None
    project_analysis: ProjectAnalysis | None
    architecture_proposal: ArchitectureProposal | None
    user_approved: bool | None
    user_modifications: str | None
    terraform_configs: dict[str, str]
    docker_compose: str | None
    deployment_result: DeploymentResult | None
    messages: list[dict]
    current_step: str
    errors: list[str]
    target_environments: list[str]
    preferred_provider: str | None
    dry_run: bool
```

#### `src/aetherdeploy/agent/graph.py` — Grafo con HIL nativo

```python
def build_graph() -> CompiledGraph:
    builder = StateGraph(AetherState)
    # nodos...
    return builder.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["confirmation"]  # HIL aquí
    )
```

#### `src/aetherdeploy/cli.py` — Dos modos de CLI

```python
@app.command()
def deploy(instruction: str, ...):
    """Modo interactivo clásico (Typer + Rich)."""
    asyncio.run(_deploy_async(instruction, ...))

@app.command(hidden=True)
def stream(stream_json: bool = typer.Option(True, "--stream-json")):
    """Modo stream JSON-lines para el frontend Ink."""
    asyncio.run(_run_stream_mode())

async def _run_stream_mode():
    """Lee comandos JSON de stdin, emite eventos JSON a stdout."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid4())}}
    
    async def emit(event: dict):
        print(json.dumps(event), flush=True)  # JSON-lines
    
    async for line in _stdin_lines():
        cmd = json.loads(line)
        if cmd["type"] == "message":
            await emit({"type": "state_change", "state": "analyzing", "message": "Analizando proyecto..."})
            async for graph_event in graph.astream({"user_message": cmd["content"]}, config):
                await _handle_and_emit(graph_event, emit)
        elif cmd["type"] == "confirm":
            graph.update_state(config, {"user_approved": cmd["value"]})
            async for graph_event in graph.astream(None, config):
                await _handle_and_emit(graph_event, emit)
```

Todos los nodos son **stubs** en esta fase.

---

## FASE 2: Análisis Inteligente de Proyectos ✅ COMPLETADA

**Objetivo**: Inspección de directorio → `ProjectAnalysis` completo. Análisis estático primero; LLM solo para enriquecer si hay ambigüedad.

**Criterio de verificación**: Directorio Node.js → detección correcta de lenguaje, framework, versión, dependencias, puertos. Tests unitarios con fixtures al 100%.

### Ficheros a crear

#### `src/aetherdeploy/analyzers/base.py`

```python
class LanguageAnalyzer(ABC):
    @abstractmethod
    def can_analyze(self, path: Path) -> bool: ...
    @abstractmethod
    def analyze(self, path: Path) -> LanguageAnalysis: ...

@dataclass
class LanguageAnalysis:
    language: str
    framework: str | None
    version: str | None
    dependencies: list[str]
    entry_points: list[str]
    exposed_ports: list[int]
    has_dockerfile: bool
    has_tests: bool
    architecture_hints: list[str]  # ["monolith", "api-only", "has-worker"]
```

#### Analizadores estáticos
- `node.py` → lee `package.json`, detecta Express/Fastify/NestJS/Next.js/Remix, versión en `.nvmrc`/`engines`
- `python.py` → lee `pyproject.toml`/`requirements.txt`/`setup.py`, detecta FastAPI/Django/Flask
- `java.py` → lee `pom.xml`/`build.gradle`, detecta Spring Boot/Micronaut/Quarkus
- `go.py` → lee `go.mod`, detecta versión y dependencias

#### `src/aetherdeploy/analyzers/detector.py` — Orquestador

```python
class ProjectDetector:
    def detect(self, path: Path) -> ProjectAnalysis:
        results = [a.analyze(path) for a in self._analyzers if a.can_analyze(path)]
        return ProjectAnalysis(primary_language=..., frameworks=..., architecture=...)
    
    async def _enrich_with_llm(self, analysis, user_message, llm: LLMBackend) -> ProjectAnalysis:
        # Infiere requisitos no evidentes (Redis, queues, etc.)
```

#### Nodos actualizados
- `discovery.py` → detecta proyecto local o solicita URL de GitHub vía estado
- `analysis.py` → usa `ProjectDetector` + enriquecimiento LLM
- `tools/github.py` → `clone_repo()` y `get_repo_info()` via GitPython

#### Tests
- `tests/fixtures/sample_node_project/` — `package.json` + Express + `Dockerfile`
- `tests/fixtures/sample_python_project/` — `pyproject.toml` + FastAPI
- `tests/unit/test_analyzers.py` — tests contra cada fixture

---

## FASE 3: Motor de Propuesta de Arquitectura y Generación IaC ✅ COMPLETADA

**Objetivo**: `ProjectAnalysis` → `ArchitectureProposal` con servicios concretos → ficheros Terraform válidos. Flujo HIL end-to-end.

**Criterio de verificación**: Proyecto Node.js → propuesta ECS+Fargate+CloudFront → confirmación del usuario → `.tf` que pasan `terraform validate`.

### Ficheros a crear

#### `src/aetherdeploy/providers/base.py` — Contratos (CRÍTICO)

```python
@dataclass
class ServiceRecommendation:
    service_name: str       # "ECS Fargate"
    purpose: str            # "compute"
    justification: str
    estimated_monthly_cost: str
    terraform_resource: str # "aws_ecs_cluster"

@dataclass
class ArchitectureProposal:
    provider: str
    region: str
    services: list[ServiceRecommendation]
    total_estimated_cost: str
    security_notes: list[str]
    scalability_notes: list[str]
    environments: dict[str, EnvironmentConfig]

class CloudProvider(ABC):
    @abstractmethod
    def recommend_architecture(self, analysis: ProjectAnalysis) -> ArchitectureProposal: ...
    @abstractmethod
    def get_terraform_backend_config(self, env: str) -> dict: ...
```

#### `src/aetherdeploy/providers/aws/services.py` — Matriz de selección

```python
COMPUTE_MATRIX = {
    ("container", "stateless", "web"): {
        "service": "ECS Fargate",
        "justification": "Serverless containers, sin gestión EC2, ideal para APIs web"
    },
    ("serverless", "event-driven", "api"): {
        "service": "Lambda + API Gateway",
        "justification": "Coste cero en idle, escala automáticamente"
    },
}
```

Matrices equivalentes para GCP (Cloud Run, GKE) y Azure (Container Apps, AKS).

#### `src/aetherdeploy/terraform/generator.py`

`TerraformGenerator` con Jinja2: produce `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf`, `backend.tf` desde templates por proveedor.

#### `src/aetherdeploy/terraform/runner.py`

`TerraformRunner.init()`, `.plan()`, `.apply()` con streaming de output via Rich Live. Salida en `.aetherdeploy/terraform/` dentro del proyecto.

#### Nodos
- `proposal.py` → el LLM decide proveedor óptimo si no se especificó; usa la matriz de servicios
- `confirmation.py` → procesa respuesta post-interrupt; si hay modificaciones redirige a `proposal`
- `generation.py` → escribe ficheros `.tf` en `.aetherdeploy/terraform/`

---

## FASE 4: Despliegue Local con Docker y Entornos con LocalStack ✅ COMPLETADA

**Objetivo**: Flujo completo funcional para `local` (Docker Compose) y `feature` (LocalStack — AWS emulado localmente). Ambos entornos implementados. La estrategia de `feature` usa **LocalStack en lugar de cloud real**.

**Criterio de verificación**:
- `--env local` → `docker-compose.yml` + app corriendo → `http://localhost:PORT` ✅
- `--env feature` → LocalStack levanta en Docker → Terraform corre contra `localhost:4566` → app funciona sobre AWS emulado → segundo HIL "¿Promover a producción?" → si aprueba → Terraform corre contra AWS real ✅

### Flujo de Entornos (actualizado)

```
┌──────────┐    ┌─────────────────────────────┐    ┌────────────────────┐
│  local   │    │         feature              │    │        prod        │
│          │    │                              │    │                    │
│  Docker  │    │  Docker Compose              │    │  Terraform         │
│  Compose │    │  + LocalStack service        │    │  → AWS / GCP /     │
│          │    │  + Terraform → localhost:4566│    │     Azure reales   │
│  Sin IaC │    │  Sin credenciales reales     │    │  Credenciales      │
│          │    │  Sin coste                   │    │  reales            │
└──────────┘    └──────────┬───────────────────┘    └────────────────────┘
                           │
                    ¿Promover a prod?
                    (segundo HIL)
                           │
                    usuario aprueba
```

### Entorno `local` ✅ (implementado)

`DockerComposer` genera `docker-compose.yml` infiriendo servicios auxiliares (PostgreSQL si detecta `psycopg2`, Redis si detecta `redis`, etc.). `DockerRunner` gestiona los contenedores.

### Entorno `feature` — LocalStack ✅ (implementado)

#### Estrategia

1. `git rev-parse --abbrev-ref HEAD` → nombre del branch → sanitizar (`feature/user-auth` → `userauth`)
2. `DockerComposer` genera un `docker-compose.feature.yml` que incluye:
   - El servicio de la aplicación (build o imagen)
   - **Un servicio `localstack`** junto a los servicios auxiliares habituales
3. Terraform corre contra el endpoint de LocalStack (`http://localhost:4566`) con credenciales ficticias
4. El usuario prueba la app sobre infraestructura AWS emulada
5. **Segundo HIL**: "Tu app funciona en el entorno feature (LocalStack). ¿Promover a producción real?"
6. Si aprueba → Terraform corre contra AWS real con credenciales reales

#### Ficheros a crear/modificar

**`src/aetherdeploy/docker/composer.py`** (actualización)
```python
def generate_feature(self, analysis, proposal) -> str:
    """Genera docker-compose para entorno feature con LocalStack."""
    compose = self.generate(analysis, proposal)  # base
    data = yaml.safe_load(compose)

    data["services"]["localstack"] = {
        "image": "localstack/localstack:latest",
        "ports": ["4566:4566"],
        "environment": [
            "SERVICES=s3,ecs,iam,cloudwatch,logs,secretsmanager,rds,elasticache",
            "DEBUG=0",
            "DOCKER_HOST=unix:///var/run/docker.sock",
        ],
        "volumes": [
            "localstack-data:/var/lib/localstack",
            "/var/run/docker.sock:/var/run/docker.sock",
        ],
        "networks": ["app-net"],
        "healthcheck": {
            "test": ["CMD", "curl", "-f", "http://localhost:4566/_localstack/health"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 10,
        },
    }

    data.setdefault("volumes", {})["localstack-data"] = {}
    # La app espera a localstack antes de arrancar
    data["services"]["app"].setdefault("depends_on", []).append("localstack")
    return yaml.dump(data, ...)
```

**`src/aetherdeploy/terraform/templates/aws/main.tf.j2`** (nuevo bloque condicional)
```hcl
{% if environment == "feature" %}
# Modo LocalStack — mismos recursos, endpoint local
provider "aws" {
  region                      = var.aws_region
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3             = "http://localhost:4566"
    ecs            = "http://localhost:4566"
    iam            = "http://localhost:4566"
    cloudwatch     = "http://localhost:4566"
    logs           = "http://localhost:4566"
    secretsmanager = "http://localhost:4566"
  }
}
{% else %}
# Modo producción — endpoints reales de AWS
provider "aws" {
  region = var.aws_region
  default_tags { tags = { ... } }
}
{% endif %}
```

**`src/aetherdeploy/agent/nodes/execution.py`** (actualización del caso `feature`)
```python
async def _deploy_feature(project_path, analysis, proposal):
    """Despliega en LocalStack vía Docker Compose + Terraform."""
    # 1. Genera compose con localstack
    compose_content = _composer.generate_feature(analysis, proposal)
    compose_file = project_path / ".aetherdeploy" / "docker-compose.feature.yml"
    compose_file.write_text(compose_content)

    # 2. Levanta LocalStack + app
    _docker.up(compose_file)
    _wait_for_localstack(timeout=60)

    # 3. Terraform contra localhost:4566
    tf_dir = project_path / ".aetherdeploy" / "terraform" / "feature"
    tf_vars = {"environment": "feature"}
    _terraform.init(tf_dir)
    _terraform.apply(tf_dir, vars=tf_vars, auto_approve=True)

    return {"success": True, "endpoints": ["http://localhost:<PORT>"], "localstack": True}
```

**`src/aetherdeploy/agent/graph.py`** — nuevo nodo `promotion` para el segundo HIL
```python
# Nuevo nodo post-feature: espera confirmación para promover a prod
builder.add_node("promotion", promotion_node)
# interrupt_before=["confirmation", "promotion"]
```

El grafo añade un segundo interrupt cuando el entorno `feature` está incluido:
```
... → execution (feature) → promotion [HIL] → execution (prod)
                            ↓ rechaza
                            END
```

#### Dependencias nuevas

```toml
# pyproject.toml — añadir
"localstack>=3.0",           # CLI y health check de LocalStack
"awscli-local>=0.22",        # awslocal (wrapper de aws CLI)
```

#### Verificación del entorno feature

```bash
# Levantar feature con LocalStack
aetherdeploy "despliega mi feature" --env feature

# LocalStack arranca en localhost:4566
# Terraform aplica contra LocalStack
# App disponible en http://localhost:<PORT>

# Segundo HIL aparece automáticamente:
# "¿Promover a producción real? (y/n)"
# → y → Terraform corre contra AWS real
```

---

## FASE 5: Multi-cloud Real, SDK Público y Observabilidad ✅ COMPLETADA

**Objetivo**: Los tres proveedores implementados. SDK Python usable programáticamente. Trazas OpenTelemetry. Listo para producción.

**Criterio de verificación**: `pip install aetherdeploy` funciona. `agent.deploy(project=".", environments=["prod"])` funciona. `terraform apply` completa en AWS, GCP y Azure.

### Ficheros a crear / completar

#### `src/aetherdeploy/sdk.py` — SDK pública con callback HIL

```python
class AetherDeploy:
    async def deploy(
        self,
        project: str = ".",
        instruction: str = "Deploy this project",
        environments: list[str] = ["prod"],
        dry_run: bool = False,
        on_proposal: Callable[[ArchitectureProposal], Awaitable[bool]] | None = None,
    ) -> DeploymentResult: ...
    
    async def analyze(self, project: str = ".") -> ProjectAnalysis: ...
    async def destroy(self, project: str = ".", environment: str = "prod") -> None: ...
```

#### Proveedores completos
- `providers/gcp/provider.py` → ADC, Cloud Run, Cloud SQL, Secret Manager
- `providers/azure/provider.py` → `DefaultAzureCredential`, Container Apps, Azure SQL, Key Vault
- `providers/__init__.py` → `ProviderRegistry.get_available_providers()` detecta credenciales automáticamente

#### Templates Terraform completos
- `templates/gcp/` → Cloud Run, Cloud SQL, Cloud Storage, Cloud CDN
- `templates/azure/` → Container Apps, Azure SQL, Blob Storage, Azure CDN

#### Observabilidad

Decorador `_traced_node` envuelve cada nodo con un span OpenTelemetry:

```python
def _traced_node(node_fn):
    async def wrapper(state: AetherState) -> dict:
        with tracer.start_as_current_span(node_fn.__name__) as span:
            span.set_attribute("step", state.get("current_step", ""))
            return await node_fn(state)
    return wrapper
```

Config adicional en `AetherConfig`:
```python
otel_enabled: bool = False
otel_endpoint: str = "http://localhost:4317"
```

---

## Árbol de Dependencias

```
Fase 1: Fundamentos y esqueleto (LLM abstraction, grafo, CLI) ✅
    └── Fase 2: Análisis inteligente (analizadores estáticos + enrichment) ✅
            └── Fase 3: Propuesta + IaC (providers, matrices, Terraform) ✅
                    └── Fase 4a: Docker local (docker-compose) ✅
                    └── Fase 4b: LocalStack para feature envs + nodo promotion ✅
                            └── Fase 5: Multi-cloud + SDK + observabilidad ✅
```

Cada fase entrega algo funcional. El proyecto nunca está "roto" entre fases.

---

## Decisiones de Diseño

| Decisión | Elección | Alternativa descartada | Razón |
|---|---|---|---|
| LLM inicial | Ollama + Gemma3 | Anthropic/OpenAI | Sin API key, sin coste, ejecución local |
| Interfaz LLM | `LLMBackend` ABC | Llamadas directas | Cambiar de Ollama a Anthropic = cambio de config, no de código |
| Orquestación | LangGraph | Anthropic Agents SDK | LangGraph = grafo con ciclos + HIL nativo |
| CLI | Typer | Click | Async nativo + type hints + Rich integrado |
| Generación IaC | Jinja2 templates | Generación LLM | Alta tasa de errores HCL con LLM; el LLM decide QUÉ, Jinja2 genera |
| Análisis | Estático primero | Solo LLM | Reduce latencia y coste; LLM solo para ambigüedad |
| Output IaC | `.aetherdeploy/terraform/` | Raíz del proyecto | Patrón `.github/`, `.devcontainer/`; gitignoreado por defecto |
| Entorno feature | LocalStack | Cloud real efímero con TTL | Sin credenciales, sin coste, mismo Terraform que prod — usuario valida antes de comprometer dinero real |
| Flujo feature→prod | Segundo HIL explícito | Auto-promote | El usuario decide conscientemente cuándo pasar de LocalStack a cloud real |

---

## Archivos Críticos

| Archivo | Por qué es crítico |
|---|---|
| `src/aetherdeploy/llm/base.py` | Punto de extensión para cambiar de backend LLM |
| `src/aetherdeploy/agent/state.py` | `AetherState` es el contrato entre todos los nodos |
| `src/aetherdeploy/agent/graph.py` | Define flujo completo y mecanismo HIL |
| `src/aetherdeploy/analyzers/detector.py` | Primer paso de valor real del sistema |
| `src/aetherdeploy/providers/base.py` | Conecta análisis → propuesta → IaC |

---

## Verificación End-to-End

```bash
# Instalar en modo desarrollo
pip install -e ".[dev]"

# Verificar CLI
aetherdeploy --help

# Test con proyecto de muestra (dry-run, sin despliegue real)
aetherdeploy "despliega mi app" --project ./tests/fixtures/sample_node_project --dry-run

# Tests unitarios
pytest tests/unit/ -v

# Tests integración (requiere Docker)
pytest tests/integration/ -v

# Validar Terraform generado
cd .aetherdeploy/terraform && terraform validate

# Test flujo local completo
aetherdeploy "despliega localmente" --env local --project ./tests/fixtures/sample_node_project
```

---

## FASE 6: Feature Parity con Prod — Best Practices y Escalabilidad ✅ EN CURSO

**Objetivo**: El entorno `feature` es un espejo funcional de producción. Los tres templates Terraform implementan autoscaling, hardening de red y gestión de secretos. El docker-compose del entorno feature incluye un load balancer local que replica el ALB / Application Gateway / Cloud Load Balancing de prod.

**Principio rector**: Cualquier bug de arquitectura que no se detecte en `feature` llegará a `prod`. Por eso feature debe emular no solo los servicios, sino también la topología de red, el LB y el comportamiento de escala.

---

### 6.1 Entorno Feature — Docker Compose prod-like

#### Cambios implementados en `docker/composer.py`

**Health check en el servicio app** ✅
```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -sf http://localhost:PORT/actuator/health || curl -sf http://localhost:PORT/health || exit 1"]
  interval: 15s
  timeout: 10s
  retries: 5
  start_period: 60s   # margen para JVM / Django startup
```

**`depends_on` condition-based en todos los servicios** ✅
```yaml
# Antes (no espera a que el servicio esté ready):
depends_on: [db, localstack]

# Después (espera healthcheck green):
depends_on:
  db:
    condition: service_healthy
  localstack:
    condition: service_healthy
```

**Servicio `nginx` como load balancer local** ✅

Simula el ALB (AWS) / Application Gateway (Azure) / Cloud Load Balancing (GCP).
```yaml
nginx:
  image: nginx:1.27-alpine
  ports: ["80:80"]
  volumes:
    - ./nginx.feature.conf:/etc/nginx/conf.d/default.conf:ro
  depends_on:
    app:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost/_lb_health"]
```

El archivo `nginx.feature.conf` se genera con `DockerComposer.generate_nginx_conf(analysis)` y lo escribe `execution.py` junto al compose:
```nginx
upstream app_upstream {
    server app:PORT;
}
server {
    listen 80;
    location / {
        proxy_pass         http://app_upstream;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
    location /_lb_health { return 200 'OK'; }
}
```

**Topología feature vs prod**

```
feature (docker-compose)             prod (Terraform → cloud)
─────────────────────────────        ─────────────────────────────────
nginx:80  ←── load balancer          ALB:80/443  ←── load balancer
  └─► app:PORT  ←── container          └─► ECS Fargate tasks (n replicas)
        └─► db:5432                           └─► RDS / Cloud SQL / PG Flex
        └─► localstack:4566               └─► AWS / GCP / Azure services
```

**Endpoints expuestos en feature**
- `http://localhost` — vía nginx (simula ALB, es la URL "prod-like")
- `http://localhost:PORT` — acceso directo a la app (debug)

---

### 6.2 AWS — Autoscaling + ALB Hardening

**Implementado en `terraform/templates/aws/main.tf.j2`** ✅

#### ALB hardening
| Propiedad | feature / staging | prod |
|---|---|---|
| `enable_deletion_protection` | `false` | `true` |
| `drop_invalid_header_fields` | `true` | `true` |
| Puerto 80 | forward directo | redirect 301 → HTTPS |
| `deregistration_delay` | 30s | 30s |
| Health check path | `/actuator/health` | `/actuator/health` |
| Health check matcher | `200-299` | `200-299` |

#### ECS Application Autoscaling
```hcl
# Target tracking CPU (scale out a 70%, scale in cooldown 5min)
resource "aws_appautoscaling_policy" "cpu" { ... }
# Target tracking Memory (scale out a 80%)
resource "aws_appautoscaling_policy" "memory" { ... }

# Capacidad mínima/máxima por entorno
min_capacity = var.environment == "prod" ? 2 : 1
max_capacity = var.environment == "prod" ? 10 : 3
```

#### Pendiente (Fase 7) ⬜
- [ ] `aws_wafv2_web_acl` para protección Layer 7 en prod
- [ ] `aws_lb_listener` HTTPS con `aws_acm_certificate`
- [ ] `aws_cloudwatch_metric_alarm` + SNS para alertas de CPU/errores
- [ ] `aws_ecs_task_definition` con `secrets` desde Secrets Manager (en lugar de `environment`)
- [ ] Multi-AZ NAT Gateways en prod (`single_nat_gateway = false`)

---

### 6.3 GCP — VPC Connector + CPU Autoscaling + Service Account

**Implementado en `terraform/templates/gcp/main.tf.j2`** ✅

#### VPC Access Connector (conectividad privada DB ↔ Cloud Run)
```hcl
resource "google_vpc_access_connector" "main" {
  ip_cidr_range = "10.8.0.0/28"
  network       = "default"
  machine_type  = "e2-micro"
  min_instances = 2
  max_instances = var.environment == "prod" ? 10 : 3
}
# Cloud Run lo referencia via annotation:
# "run.googleapis.com/vpc-access-connector" = google_vpc_access_connector.main.name
# "run.googleapis.com/vpc-access-egress"    = "private-ranges-only"
```

#### Service Account dedicado
```hcl
resource "google_service_account" "app" {
  account_id = "${var.project_name}-${var.environment}-sa"
}
# Cloud Run corre con esta SA en lugar de la SA default de Compute Engine
```

#### CPU Throttling y autoscaling por entorno
| Anotación | feature | prod |
|---|---|---|
| `maxScale` | 5 | 20 |
| `minScale` | 0 | 1 |
| `cpu-throttling` | true (cold start) | false (siempre encendida) |
| `startup-cpu-boost` | true | true |

#### Pendiente (Fase 7) ⬜
- [ ] `google_cloud_armor_security_policy` (WAF)
- [ ] `google_sql_ssl_cert` para conexión TLS a Cloud SQL
- [ ] `google_secret_manager_secret` para credenciales DB (en lugar de variables)
- [ ] `google_cloud_run_domain_mapping` para dominio custom
- [ ] Liveness / readiness probes en Cloud Run v2 (`google_cloud_run_v2_service`)

---

### 6.4 Azure — Scaling HTTP + Key Vault para secretos DB

**Implementado en `terraform/templates/azure/main.tf.j2`** ✅

#### Key Vault para la contraseña de DB
```hcl
resource "azurerm_key_vault" "main" {
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = var.environment == "prod" ? 90 : 7
}
resource "azurerm_key_vault_secret" "db_password" { ... }
# La contraseña nunca aparece en texto plano en el state de Terraform
```

#### Container Apps scaling HTTP
```hcl
custom_scale_rule {
  name             = "http-scaling"
  custom_rule_type = "http"
  metadata = {
    concurrentRequests = var.environment == "prod" ? "50" : "30"
  }
}
```

#### Probes y HTTPS enforcement
```hcl
liveness_probe  { path = "/actuator/health" ... }
readiness_probe { path = "/actuator/health" ... }

ingress {
  allow_insecure_connections = var.environment != "prod"  # prod = solo HTTPS
}
```

#### Recursos por entorno
| Recurso | feature | prod |
|---|---|---|
| CPU | 0.25 vCPU | 0.5 vCPU |
| Memory | 0.5 Gi | 1 Gi |
| min_replicas | 0 | 1 |
| max_replicas | 5 | 20 |
| DB SKU | B_Standard_B1ms | GP_Standard_D2s_v3 |
| DB backup | 7 días | 35 días |
| Geo-redundant backup | false | true |

#### Pendiente (Fase 7) ⬜
- [ ] `azurerm_application_gateway` con WAF_v2 para prod
- [ ] `azurerm_private_endpoint` para conectividad privada Container Apps → PostgreSQL
- [ ] `azurerm_container_app_environment_dapr_component` para secret injection sin SDK
- [ ] Managed Identity en lugar de Key Vault access policy (más seguro)
- [ ] `azurerm_monitor_metric_alert` para CPU/memory/requests

---

### 6.5 Árbol de dependencias de features (Escalabilidad)

```
┌─────────────────────────────────────────────────────────────┐
│  Autoscaling — todos los providers                          │
│  AWS: AppAutoscaling (CPU 70% + Memory 80%)                 │
│  GCP: Knative annotations (maxScale/minScale/cpu-boost)     │
│  Azure: Container Apps HTTP scaling (concurrentRequests)    │
└────────────────────────────┬────────────────────────────────┘
                             │ requiere
┌────────────────────────────▼────────────────────────────────┐
│  Health Checks — todos los providers                        │
│  AWS: ALB target group /actuator/health matcher 200-299     │
│  GCP: liveness_probe + startup_probe en Cloud Run           │
│  Azure: liveness_probe + readiness_probe en Container App   │
│  Feature: app healthcheck en docker-compose (start_period)  │
└────────────────────────────┬────────────────────────────────┘
                             │ requiere
┌────────────────────────────▼────────────────────────────────┐
│  Gestión de Secretos — todos los providers                  │
│  AWS: Secrets Manager (db-password, api-keys)               │
│  GCP: Secret Manager (pendiente)                            │
│  Azure: Key Vault ✅ (db-password via soft-delete + purge)  │
└─────────────────────────────────────────────────────────────┘
```

---

### 6.6 Checklist Global de Best Practices

| Práctica | AWS | GCP | Azure | Feature (local) |
|---|:---:|:---:|:---:|:---:|
| Load Balancer en todos los entornos | ✅ ALB | ✅ Cloud LB | ✅ Container Apps ingress | ✅ nginx |
| Health checks en LB y app | ✅ | ✅ | ✅ | ✅ |
| Autoscaling CPU-based | ✅ | ✅ | ✅ | — |
| Autoscaling HTTP-based | ✅ (ALB req/target) | ✅ (knative target) | ✅ | — |
| Min replicas en prod ≥ 2 | ✅ | ✅ | ✅ | — |
| Zero replicas en feature/dev | ✅ | ✅ (minScale=0) | ✅ | — |
| Secretos gestionados (no env vars) | ✅ Secrets Mgr | ⬜ pendiente | ✅ Key Vault | — |
| Red privada para DB | ✅ VPC subnets | ✅ VPC Connector | ⬜ pending private EP | ✅ docker network |
| HTTPS forzado en prod | ✅ redirect 301 | ✅ Cloud Run default | ✅ allow_insecure=false | — |
| Protection contra borrado accidental | ✅ deletion_protection | ✅ deletion_protection | ✅ purge_protection | — |
| Logs centralizados | ✅ CloudWatch | ✅ Cloud Logging (auto) | ✅ Log Analytics | ✅ stdout/stderr |
| Trazas OpenTelemetry | ✅ OTEL | ✅ OTEL | ✅ OTEL | ✅ OTEL |
| Service Account / IAM mínimo | ✅ ECS execution role | ✅ SA dedicado | ⬜ Managed Identity | — |
| Startup grace period | ✅ scale_out_cooldown | ✅ startup-cpu-boost | ✅ initial_delay | ✅ start_period |
| depends_on condition-based | — | — | — | ✅ service_healthy |

