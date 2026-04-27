# Code Review — AetherDeploy

Revisión completa del código fuente. Los problemas están ordenados por gravedad (crítico → menor).

---

## 1. Problemas de Seguridad

### 1.1 Credenciales escritas directamente en `os.environ` (CRÍTICO)

**Archivo:** `cli.py:1178-1182`

```python
elif cmd["type"] == "credentials":
    values: dict = cmd.get("values", {})
    for key, value in values.items():
        if key and value:
            os.environ[key] = value   # ← peligroso
```

Las credenciales recibidas del frontend se inyectan en el entorno del proceso sin ningún tipo de sanitización, cifrado ni control de vida útil. Permanecen en memoria durante toda la sesión y se propagan a todos los procesos hijo (Terraform, Docker). Si el proceso hace un volcado de memoria o un log de entorno, las credenciales quedan expuestas.

**Mejora:** Usar un almacén de credenciales efímero en memoria (diccionario separado) que solo se pasa a los subprocesos que lo necesitan mediante argumentos explícitos, y borrarlo después de cada operación.

---

### 1.2 `_redact_secrets` es O(n·m) y puede perderse valores (ALTO)

**Archivo:** `execution.py:769-791`

La función itera sobre `os.environ` completo por cada cadena que sanitiza. Si el output de Terraform es largo, el coste es `len(output) × len(os.environ)`. Además, solo redacta valores que estén actualmente en `os.environ`, lo que significa que si se rotaron las credenciales durante la sesión, la redacción falla.

**Mejora:** Pre-compilar un `re.Pattern` único con todos los valores secretos al inicio de la sesión, o usar una lista negra basada en patrones (no en valores concretos).

---

### 1.3 `.env` con credenciales reales en el directorio del proyecto

**Archivo:** `.env`

El archivo `.env` existe en el directorio de trabajo y es leído por `pydantic-settings`. Si el proyecto se clona o se comparte sin revisar `.gitignore`, las credenciales reales se exponen. El `.gitignore` actual debe verificarse para confirmar que `.env` está excluido.

---

## 2. Problemas Arquitectónicos

### 2.1 `cli.py` es un fichero God de ~1600 líneas (ALTO)

`cli.py` mezcla demasiadas responsabilidades:
- Comandos CLI (typer)
- Protocolo de stream JSON-lines para la UI de Ink
- Máquina de estados de onboarding
- Detección de intención NLU (regex)
- Validación de credenciales
- Clonado de repositorios GitHub

`_stream_mode` por sí sola tiene ~600 líneas con estado gestionado por 10+ variables `nonlocal`. Esto hace que:
- Sea prácticamente imposible escribir tests unitarios para el stream mode.
- Cualquier cambio en el flujo de credenciales o promoción toque código no relacionado.

**Mejora:** Extraer al menos tres módulos:
- `cli/stream.py` — protocolo JSON-lines y máquina de estados
- `cli/nlu.py` — detección de intención
- `cli/credentials.py` — validación y comprobación de credenciales

---

### 2.2 Dos rutas de promoción con lógica diferente (ALTO)

**Archivo:** `cli.py:1223-1290`

Existen dos caminos para manejar la promoción a producción:
1. **Graph-based** (`promoted_to_prod` → `_route_after_promotion` → `execution`): usa el grafo LangGraph.
2. **Manual** (`_manual_promotion_pending` → `run_action_from_current("deploy", envs_override=["prod"])`): bypasea el grafo completamente.

La ruta manual se activa cuando el deployment de feature viene del comando `/deploy` en modo stream, mientras que la ruta del grafo se activa cuando el deployment inicial fue iniciado desde el grafo. Las dos rutas tienen diferente manejo de errores y credenciales, lo que genera inconsistencias silenciosas.

**Mejora:** Unificar en una sola ruta. La promoción siempre debería ir a través del grafo (estado `promote_approved` → `execution`).

---

### 2.3 Singletons de módulo en `execution.py` dificultan los tests (MEDIO)

**Archivo:** `execution.py:17-19`

```python
_composer = DockerComposer()
_docker = DockerRunner()
_terraform = TerraformRunner()
```

Estos singletons se instancian al importar el módulo, lo que impide reemplazarlos con mocks en tests sin recurrir a `unittest.mock.patch`. También significa que la configuración (binario de terraform, etc.) se fija en el momento de importación.

**Mejora:** Inyectar las dependencias como parámetros de función o usar un patrón de factoría lazy que respete la configuración vigente.

---

### 2.4 `_emitter_var` como acoplamiento implícito entre módulos (MEDIO)

**Archivo:** `execution.py:25`, importado en `analysis.py:8`, `proposal.py:40`

El `ContextVar` de emisión de eventos está definido en `execution.py` pero es importado por nodos que no deberían depender de ejecución. Esto crea un acoplamiento inverso en el grafo de dependencias.

**Mejora:** Mover `_emitter_var` a un módulo de contexto dedicado (`agent/context.py`) del que todos los nodos puedan importar sin depender unos de otros.

---

## 3. Bugs

### 3.1 El enriquecimiento LLM solo propaga hints al primer servicio (ALTO)

**Archivo:** `analysis.py:23-46`

```python
for i, svc in enumerate(topology.services):
    if i == 0:
        updated_services.append(
            type(svc)(hints=new_hints, ...)  # ← solo el primero
        )
    else:
        updated_services.append(svc)  # ← los demás no se actualizan
```

El comentario dice "best-effort: first service gets all" pero esto es un bug real: en proyectos multiservicio, el perfil de complejidad se recalcula con los nuevos hints pero los servicios 2..n conservan sus hints originales. La propuesta de arquitectura resultante puede ser inconsistente.

**Mejora:** Distribuir los hints relevantes a cada servicio o usar `all_hints` como fuente de verdad a nivel de topología (que ya existe).

---

### 3.2 `_validate_required_credentials` tiene un `return` prematuro en el bucle (MEDIO)

**Archivo:** `cli.py:1400-1405`

```python
    elif env in ("prod", "staging"):
        ok, msg = await _validate_provider_credentials(provider)
        if not ok:
            return ok, msg
        return ok, msg  # ← sale en el primer env válido
```

La función retorna en el primer entorno `prod`/`staging` que valida correctamente, sin comprobar los demás. Si el usuario pasa `["prod", "staging"]`, solo se valida `prod`.

**Mejora:**

```python
    elif env in ("prod", "staging"):
        ok, msg = await _validate_provider_credentials(provider)
        if not ok:
            return ok, msg
# return True fuera del bucle
return True, "No cloud credentials required for this flow."
```

---

### 3.3 Atributo dinámico en dataclass tipado (MEDIO)

**Archivo:** `cli.py:333`

```python
topology._llm_reasoning = enrichment.reasoning  # type: ignore[attr-defined]
```

Se añade un atributo dinámico a un `@dataclass` que no lo declara. El `# type: ignore` silencia el error de mypy pero la aplicación puede fallar si el dataclass usa `__slots__` o si el atributo se accede antes de ser establecido. También rompe la serialización de LangGraph si se guarda este objeto en un checkpoint.

**Mejora:** Añadir `llm_reasoning: str | None = field(default=None)` al dataclass `ApplicationTopology`.

---

### 3.4 `_close_backend` crea una task sin awaitar (MENOR)

**Archivo:** `proposal.py:410-424`

```python
if loop.is_running():
    loop.create_task(close())   # ← fire-and-forget
```

La task de cierre del backend puede no ejecutarse antes de que el loop termine, dejando conexiones abiertas. En producción esto no suele causar problemas visibles, pero sí genera warnings de "coroutine was never awaited" en tests.

**Mejora:** Usar `asyncio.ensure_future(close())` y registrar un error si falla, o simplemente no cerrar si el loop ya está en marcha (las conexiones httpx se cierran al finalizar el proceso de todas formas).

---

### 3.5 `_wait_for_localstack` usa sleep bloqueante dentro de `async` (MENOR)

**Archivo:** `execution.py:568-581`

```python
def _wait_for_localstack(project_path: Path, timeout: int = 60) -> dict:
    import time
    while time.time() < deadline:
        ...
        time.sleep(3)   # ← bloquea el event loop
```

Aunque esta función es síncrona, se llama desde `_deploy_feature` que es `async`. El `time.sleep(3)` bloquea el event loop durante todo el polling.

**Mejora:** Convertir a función async con `asyncio.sleep(3)` o ejecutarla en un executor con `loop.run_in_executor`.

---

## 4. Calidad del Código

### 4.1 `_detect_intent` siempre devuelve `"deploy"` por defecto (MEDIO)

**Archivo:** `cli.py:101-131`

Si el usuario escribe cualquier mensaje que no contenga keywords de plan/init/destroy, la función retorna `action = "deploy"`. Esto puede convertir mensajes de seguimiento ("¿cuánto cuesta esto?") en comandos de despliegue cuando `initialized=False`.

La función `_detect_explicit_action` fue creada como corrección de este problema para el modo `initialized=True`, pero el primer mensaje siempre pasa por `_detect_intent` sin esta protección.

---

### 4.2 `analyze` command llama a método privado (`_should_enrich`) (MENOR)

**Archivo:** `cli.py:325`

```python
if use_llm or enricher._should_enrich(topology.all_hints, topology.service_count, path):
```

Se accede a un método privado desde fuera de la clase. Si la interfaz interna cambia, este código falla silenciosamente o produce resultados incorrectos.

**Mejora:** Hacer `_should_enrich` público o exponer la lógica a través de un parámetro del método `enrich()`.

---

### 4.3 `get_config()` es un singleton que no se puede resetear en tests (MENOR)

**Archivo:** `config.py:33-41`

```python
_config: AetherConfig | None = None

def get_config() -> AetherConfig:
    global _config
    if _config is None:
        _config = AetherConfig()
    return _config
```

Los tests que modifican variables de entorno después de que `get_config()` fue llamado por primera vez obtienen la configuración antigua. La variable global tampoco se resetea entre tests.

**Mejora:** Usar `@functools.lru_cache(maxsize=1)` con `cache_clear()` disponible para tests, o pasar la configuración como parámetro explícito en los constructores que la necesitan.

---

### 4.4 `_PROVIDER_MAP` tiene una clave duplicada (MENOR)

**Archivo:** `cli.py:79-83`

```python
_PROVIDER_MAP = {
    ...
    "googlecloud": "gcp", "googlecloud": "gcp",   # ← duplicado
    ...
}
```

La clave `"googlecloud"` aparece dos veces. Python sobrescribe la primera con la segunda (sin error), pero indica descuido en el código.

---

## 5. Dependencias

### 5.1 `boto3` y `opentelemetry` son dependencias obligatorias innecesariamente (ALTO)

**Archivo:** `pyproject.toml:11-23`

`boto3` se lista como dependencia core pero solo se usa en la validación de credenciales AWS. Un usuario que despliegue en GCP o Azure instala ~15MB extra de AWS SDK sin necesidad.

Lo mismo aplica a `opentelemetry-sdk` y `opentelemetry-exporter-otlp` — solo se usan si `AETHER_OTEL_ENABLED=true`.

`google-auth` y `azure-identity` se usan con `try/except ImportError` pero **no están en `pyproject.toml`**, lo que significa que GCP y Azure pueden fallar silenciosamente en entornos limpios.

**Mejora:**

```toml
[project.optional-dependencies]
aws = ["boto3>=1.34"]
gcp = ["google-auth>=2.0"]
azure = ["azure-identity>=1.16"]
otel = ["opentelemetry-sdk>=1.25", "opentelemetry-exporter-otlp>=1.25"]
all = ["aetherdeploy[aws,gcp,azure,otel]"]
```

---

### 5.2 `docker` SDK como dependencia obligatoria (MENOR)

**Archivo:** `pyproject.toml:19`

El SDK de Python para Docker (`docker>=7.0`) solo se usa si el usuario despliega en entorno `local` o `feature`. Los usuarios que solo usan cloud (prod) lo instalan innecesariamente.

---

## 6. Oportunidades de Mejora

### 6.1 Añadir prompt caching al backend Anthropic (MEJORA)

**Archivo:** `llm/anthropic.py`

El `COST_OPTIMIZE_SYSTEM` y `ANALYSIS_ENRICHMENT_SYSTEM` son prompts largos y estáticos que se envían en cada llamada. La API de Anthropic soporta **prompt caching** (cabecera `cache_control: {"type": "ephemeral"}`), lo que reduciría la latencia y el coste ~90% en llamadas repetidas.

```python
kwargs["system"] = [
    {
        "type": "text",
        "text": system,
        "cache_control": {"type": "ephemeral"},
    }
]
```

---

### 6.2 El catálogo `SERVICE_ALTERNATIVES` solo existe para AWS (MEJORA)

**Archivo:** `proposal.py:167`

```python
use_cost_optimize = provider_name == "aws"
```

GCP y Azure usan el prompt de review legacy sin optimización de costes. El sistema de catálogos está bien diseñado y solo necesita que alguien añada `SERVICE_ALTERNATIVES` equivalentes para `providers/gcp/services.py` y `providers/azure/services.py`.

---

### 6.3 El grafo LangGraph no tiene timeout por nodo (MEJORA)

Si un nodo se cuelga (e.g., Terraform tarda más del timeout configurado, pero el timeout de streaming falla silenciosamente), el grafo queda bloqueado indefinidamente. No hay ningún mecanismo de timeout a nivel de grafo que mate la sesión.

**Mejora:** Envolver cada `astream` con `asyncio.wait_for(graph.astream(...), timeout=max_session_seconds)`.

---

### 6.4 `_sanitize` en observabilidad itera `os.environ` por cada string (MEJORA)

**Archivo:** `observability.py:99-116`

Para cada string en un log event, la función itera sobre todos los pares de `os.environ` filtrando por nombre de clave secreta. En producción, con logs frecuentes y muchas variables de entorno, esto tiene un coste no trivial.

**Mejora:** Pre-calcular al inicio de la sesión un conjunto de valores sensibles y compilar un único `re.Pattern` con ellos. Actualizarlo solo cuando se modifique `os.environ`.

---

### 6.5 Sin validación del proveedor en `AetherConfig` (MEJORA)

**Archivo:** `config.py:16`

```python
default_provider: Literal["aws", "gcp", "azure"] = "aws"
```

`default_provider` está tipado como `Literal` pero `llm_backend` es `str` sin restricciones. Un valor inválido (`AETHER_LLM_BACKEND=openrouter`) falla con un error críptico dentro de `LLMBackendFactory.create()`. Añadir validación en pydantic:

```python
from pydantic import field_validator

@field_validator("llm_backend")
@classmethod
def check_backend(cls, v: str) -> str:
    valid = {"ollama", "anthropic"}
    if v not in valid:
        raise ValueError(f"llm_backend must be one of {valid}, got '{v}'")
    return v
```

---

### 6.6 GitHub clone va a `/tmp` sin cleanup (MEJORA)

**Archivo:** `cli.py:1462-1466`

```python
async def _clone_github_repo(url: str) -> Path:
    dest = Path(tempfile.gettempdir()) / "aetherdeploy-repos"
    dest.mkdir(parents=True, exist_ok=True)
    ...
```

Los repositorios clonados en `/tmp/aetherdeploy-repos` nunca se borran. En sesiones largas o con repositorios grandes, esto consume espacio de disco silenciosamente.

**Mejora:** Usar `tempfile.mkdtemp()` dentro de un context manager y registrar el path para cleanup al finalizar la sesión.

---

## 7. Tests

### 7.1 El stream mode no tiene tests (CRÍTICO)

`_stream_mode` es la ruta principal de integración con la UI y tiene ~600 líneas de lógica de estado. No hay ningún test que verifique el protocolo JSON-lines, los transitions de estado, ni el manejo de errores.

### 7.2 `LLMAnalysisEnricher` no tiene tests de integración con LLM real

Solo se testea el path de fallback. Los tests deberían verificar que el LLM respeta el vocabulario de hints (`_VALID_HINTS`) y que hints inválidos son rechazados correctamente.

### 7.3 Sin tests para `_apply_service_overrides`

La lógica de aplicar overrides del LLM (que modifica la propuesta in-place) es crítica para la funcionalidad de optimización de costes pero no aparece cubierta en los tests existentes.

---

## Resumen de Prioridades

| Prioridad | Ítem | Impacto | Estado |
|-----------|------|---------|--------|
| P0 | 1.1 Credenciales en `os.environ` | Seguridad | ✅ Implementado — whitelist + `_ALLOWED_CREDENTIAL_KEYS` |
| P0 | 7.1 Sin tests del stream mode | Calidad | Pendiente |
| P1 | 2.1 God file `cli.py` | Mantenibilidad | ✅ NLU → `cli_nlu.py`, credenciales → `cli_credentials.py` |
| P1 | 3.1 Hints solo al primer servicio | Correctness | ✅ Implementado — hints propagados a todos los servicios |
| P1 | 5.1 `boto3`/`otel` dependencias obligatorias | Distribución | ✅ Implementado — extras `[aws]`, `[gcp]`, `[azure]`, `[otel]` |
| P2 | 2.2 Dos rutas de promoción | Correctness | Pendiente (requiere refactor profundo del stream mode) |
| P2 | 3.2 `return` prematuro en validación | Bug | ✅ Implementado |
| P2 | 6.1 Prompt caching Anthropic | Performance/Coste | ✅ Implementado — `cache_control: ephemeral` en system prompt |
| P3 | 2.3 Singletons de módulo | Testabilidad | Pendiente |
| P3 | 3.3 Atributo dinámico en dataclass | Type safety | ✅ Implementado — campo `llm_reasoning` en `ApplicationTopology` |
| P3 | 2.4 `_emitter_var` acoplamiento | Arquitectura | ✅ Implementado — movido a `agent/context.py` |
| P3 | 3.4 `_close_backend` sin await | Bug | ✅ Implementado |
| P3 | 3.5 `_wait_for_localstack` bloquea event loop | Bug | ✅ Implementado — convertido a async |
| P3 | 4.4 Clave duplicada `googlecloud` | Bug | ✅ Implementado |
| P4 | 6.3 Sin timeout de grafo | Robustez | Pendiente |
| P4 | 6.5 Sin validación `llm_backend` | Config | ✅ Implementado — `field_validator` + `reset_config()` |
| P4 | 6.6 Clone sin cleanup | Recursos | ✅ Implementado — `atexit` cleanup con `tempfile.mkdtemp` |
