# AetherDeploy — Revisión de problemas de UX, feedback e interacción

> Revisión exhaustiva del proyecto. El objetivo es que la interacción usuario → CLI → agentes sea
> natural, real y con feedback constante. Actualmente hay bugs críticos, flujos rotos y ausencia
> total de validación de credenciales.

---

## 🔴 CRÍTICOS — Bugs que rompen el flujo

### 1. `NameError`: `emit` no está definida en `execution_node`

**Archivo:** `src/aetherdeploy/agent/nodes/execution.py`

El nodo de ejecución usa `emit(...)` directamente sin asignar el valor del `ContextVar`:

```python
# BUGGY — emit no existe en este scope
async def execution_node(state: AetherState) -> dict:
    if dry_run:
        emit({"type": "progress", ...})   # NameError aquí
    ...
    result = await _deploy_local(project_path, analysis, proposal, emit)  # NameError aquí
```

**Fix requerido:** añadir `emit = _emitter_var.get()` al inicio de `execution_node`.

Consecuencia: **todos los eventos de progreso del despliegue se pierden** (o el nodo lanza
`NameError` y el grafo falla silenciosamente).

---

### 2. Ruta del virtualenv incorrecta en el frontend

**Archivo:** `cli-ui/src/hooks/useAgent.ts` (línea ~38)

```typescript
const VENV_PYTHON = join(PROJECT_ROOT, "venv/bin/python3")   // ❌ incorrecto
```

El workspace usa `.venv/`, no `venv/`. El check `existsSync(VENV_PYTHON)` falla, se cae a
`"python3"` del sistema que puede no tener `aetherdeploy` instalado.

**Fix:** `join(PROJECT_ROOT, ".venv/bin/python3")`

---

### 3. Credenciales fake son aceptadas sin ninguna validación

**Archivos:** `src/aetherdeploy/agent/nodes/execution.py` → `check_prod_credentials`,
`src/aetherdeploy/cli.py` → handler `credentials`

`check_prod_credentials` sólo comprueba **presencia** de la variable de entorno, no su validez:

```python
has_env = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
if not has_env and not has_file:
    return {...}   # pide credenciales
# Si has_env es verdadero (cualquier string), devuelve None → "todo ok"
```

El handler `credentials` en `_stream_mode` simplemente inyecta el valor recibido:
```python
os.environ[key] = value
emit({"type": "message", ..., "content": "Credenciales recibidas. Continuando..."})
```

**El sistema dice "todo hecho" con `AWS_ACCESS_KEY_ID=fake`.** La falla real llega minutos después
cuando Terraform intenta conectar a AWS.

**Fix requerido:**
- Llamar `AWSProvider().verify_credentials()` (método ya implementado con boto3/STS) antes de
  continuar.
- Para GCP: parsear el JSON y verificar que tenga los campos requeridos.
- Para Azure: hacer una llamada test a la API de gestión.
- Emitir error inmediato si las credenciales son inválidas, en lugar de fallar en Terraform.

> **Nota:** `AWSProvider.verify_credentials()` ya existe pero **nunca se llama** en el flujo de
> credenciales.

---

### 4. `_stream_mode` no tiene manejo de excepciones

**Archivo:** `src/aetherdeploy/cli.py`

```python
async def run_graph(state_or_none):
    ...
    async for event in graph.astream(state_or_none, config):
        ...
```

Si cualquier nodo del grafo lanza una excepción (error de red, LLM no disponible, permiso de
archivo, etc.), el proceso Python muere **sin emitir ningún evento de error**. El frontend ve un
`exit` inesperado y simplemente pone el estado en `idle`.

**Fix:** envolver `run_graph` en `try/except` y emitir `{"type": "error", "message": ...,
"recoverable": false}` antes de salir.

---

### 5. El proceso backend stderr es invisible en la UI

**Archivo:** `cli-ui/src/hooks/useAgent.ts`

```typescript
const proc = spawn(PYTHON_BIN, BACKEND_MODULE, {
  stdio: ["pipe", "pipe", "inherit"],   // stderr va a la terminal del sistema operativo
})
```

Las excepciones Python, warnings de LangGraph y errores de importación salen por `stderr` al
terminal que lanzó la app, **completamente fuera del UI de Ink**. El usuario no ve nada raro.

**Fix:** capturar stderr (`"pipe"`) y redirigirlo como eventos `error` al UI, o al menos
guardarlos en un buffer para diagnóstico.

---

### 6. El proceso `exit` no verifica el código de salida

**Archivo:** `cli-ui/src/hooks/useAgent.ts`

```typescript
proc.on("exit", () => {
  setAgentState("idle")   // igual si exit code 0 o 1
})
```

Si el backend Python crashea, el usuario ve el estado `idle` y puede seguir escribiendo en la
caja de chat sin saber que el backend ya no responde.

**Fix:**
```typescript
proc.on("exit", (code) => {
  if (code !== 0) {
    addMessage({ role: "error", content: `El agente terminó inesperadamente (código ${code}).` })
    setAgentState("error")
  } else {
    setAgentState("idle")
  }
})
```

---

## 🟠 GRAVES — Flujos rotos o sin feedback real

### 7. Terraform no hace streaming de su output

**Archivo:** `src/aetherdeploy/terraform/runner.py`

`TerraformRunner._run()` usa `subprocess.run()` bloqueante:

```python
result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workdir))
```

`terraform init` tarda 1-3 minutos descargando providers. `terraform apply` puede tardar 10-15
minutos. Durante todo ese tiempo el usuario ve una barra de progreso **estática** con el
porcentaje congelado.

**Fix:** usar `subprocess.Popen` con lectura línea a línea + callback `emit` para transmitir las
líneas al frontend en tiempo real.

---

### 8. `terraform plan` se anuncia pero no se ejecuta

**Archivo:** `src/aetherdeploy/agent/nodes/execution.py` → `_deploy_prod`

```python
emit({"type": "progress", "step": "tf_plan", "percentage": 80})
emit({"type": "progress", "step": "tf_apply", "percentage": 84})
apply = _terraform.apply(tf_dir, auto_approve=True)
```

Se emite el evento de "planificando" pero **nunca se llama a `_terraform.plan()`**. El usuario
no sabe qué va a crear/destruir Terraform antes de que se ejecute `apply`.

**Fix:** ejecutar `terraform plan` y emitir su output como mensajes antes de `apply`.

---

### 9. Errores de Terraform truncados a 300 caracteres

**Archivo:** `src/aetherdeploy/agent/nodes/execution.py`

```python
return {"success": False, "error": f"terraform init falló: {init.stderr[:300]}"}
return {"success": False, "error": f"terraform apply falló: {apply.stderr[:300]}"}
```

Un error típico de Terraform (credenciales inválidas, región incorrecta, quota excedida) puede
tener la causa real en los primeros 300 chars... o no. El mensaje se corta arbitrariamente.

**Fix:** enviar el error completo o, al menos, extraer las líneas `Error:` de Terraform
(siempre al final) en lugar de truncar desde el principio.

---

### 10. Sin timeout en operaciones de Terraform

**Archivo:** `src/aetherdeploy/terraform/runner.py`

`subprocess.run()` y `asyncio.create_subprocess_exec()` no tienen timeout. Un `terraform apply`
que se cuelga (error de red, credenciales mal configuradas, estado bloqueado) congela el proceso
Python indefinidamente. No hay feedback al usuario ni forma de cancelar desde la UI.

**Fix:** añadir `timeout` configurable y emitir un error si se supera.

---

### 11. `discovery_needs_github` es un callejón sin salida

**Archivo:** `src/aetherdeploy/cli.py` → `_stream_mode`

Cuando no hay proyecto local, el nodo emite:
```python
{"role": "assistant", "content": "¿Tienes la URL de un repositorio GitHub?"}
```

Pero el handler de mensajes de seguimiento hace:
```python
elif cmd["type"] == "message" and initialized:
    emit({"type": "message", "role": "assistant", "content": "Recibido."})
```

No hay ningún código que tome la respuesta del usuario con la URL de GitHub y la use para
reiniciar el grafo. **La sesión queda completamente bloqueada.**

---

### 12. El LLM nunca se usa realmente

**Archivos:** `src/aetherdeploy/agent/nodes/analysis.py`,
`src/aetherdeploy/agent/nodes/proposal.py`

`analysis_node` usa únicamente detección estática de archivos. `proposal_node` usa matrices de
decisión hardcodeadas. Los backends LLM (`OllamaBackend`, `AnthropicBackend`) están implementados
pero **ningún nodo del agente los instancia ni los llama**.

La propuesta que el usuario aprueba es siempre la misma para el mismo stack tecnológico,
independientemente de lo que el usuario haya descrito en lenguaje natural.

---

### 13. Sin feedback entre nodos del grafo

Entre `discovery → analysis → proposal`, no hay progreso visible. El estado pasa de `analyzing`
a nada mientras el código estático o LLM procesa. El spinner gira sin más información.

**Fix:** emitir mensajes específicos al inicio de cada nodo:
- "Escaneando archivos del proyecto..."
- "Detectando stack tecnológico..."
- "Calculando arquitectura óptima..."

---

### 14. Estado `error` no tiene UI de recuperación

**Archivo:** `cli-ui/src/App.tsx`

Cuando `agentState === "error"`, el `StatusBar` muestra "Se produjo un error" pero:
- No hay botón/atajo de teclado para reintentar
- No hay instrucciones de qué hacer
- El `ChatInput` queda habilitado pero el backend puede estar muerto
- La barra de progreso puede seguir visible si `realProgress !== null`

**Fix:** mostrar un panel de error con el último mensaje de error y opciones claras (reintentar /
salir).

---

## 🟡 IMPORTANTES — UX degradada

### 15. La confirmación `y/n` se activa con una sola tecla sin Enter

**Archivo:** `cli-ui/src/components/ChatInput.tsx`

```typescript
if (input.toLowerCase() === "y" || input === "s") onConfirm(true)
if (input.toLowerCase() === "n") onConfirm(false)
```

Pulsar `Y` una vez confirma inmediatamente, sin Enter. El usuario puede confirmar
accidentalmente. Esto es especialmente peligroso para la confirmación de promoción a producción.

**Fix:** requerir Enter después de `y/n`, o mostrar el carácter escrito y esperar Enter como
`CredentialsInput`.

---

### 16. `CredentialsInput` no indica por qué no avanza con campo vacío

**Archivo:** `cli-ui/src/components/CredentialsInput.tsx`

```typescript
if (key.return) {
  if (!current.trim()) return   // silencio total
```

Si el usuario pulsa Enter con campo vacío, no pasa nada. No hay mensaje de error.

**Fix:** mostrar `"Este campo es obligatorio"` en rojo debajo del campo.

---

### 17. No se valida el formato básico de credenciales antes de enviar

Las claves AWS tienen formato conocido: `AKIA[0-9A-Z]{16}` (access key) y 40 caracteres
alfanuméricos (secret). Una validación básica de formato en el frontend evitaría errores obvios
(espacios al final, copia incompleta) antes de llegar al backend.

---

### 18. Progreso ficticio desconectado de la realidad

**Archivo:** `cli-ui/src/App.tsx`

```typescript
const STATE_PROGRESS: Record<string, number> = {
  analyzing: 20,
  planning:  55,
  deploying: 80,
}
```

Cuando Terraform está aplicando, la barra muestra siempre "80%" aunque hayan pasado 10 minutos.
Esto es más frustrante que no tener barra, porque crea falsa expectativa.

**Fix:** usar los eventos `progress` reales del backend, o mostrar un spinner indeterminado en
lugar de un porcentaje ficticio.

---

### 19. No hay confirmación visible de que las credenciales se validaron

Tras introducir credenciales, el único feedback es "Credenciales recibidas. Continuando..." (sin
ninguna verificación real). El usuario no sabe si sus credenciales son correctas hasta que
Terraform falla minutos después.

**Fix:** tras recibir credenciales, ejecutar una llamada de verificación ligera (ej. `sts
get-caller-identity` para AWS) y emitir:
- ✓ "Credenciales AWS verificadas (cuenta: 123456789012)"
- ✗ "Credenciales inválidas: Access Denied"

---

### 20. El mensaje de seguimiento no hace nada (`"Recibido."`)

**Archivo:** `src/aetherdeploy/cli.py`

```python
elif cmd["type"] == "message" and initialized:
    emit({"type": "message", "role": "assistant", "content": "Recibido."})
```

Si el usuario escribe algo después de que el agente termine, obtiene "Recibido." y nada más.
No hay chat real, no hay historial de contexto, no hay continuación del despliegue.

---

### 21. El backend `ready` se marca inmediatamente sin esperar al proceso

**Archivo:** `cli-ui/src/hooks/useAgent.ts`

```typescript
setReady(true)   // justo después de spawn(), antes de que Python arranque
```

Si Python tarda en iniciar (import de LangGraph, pydantic-settings, etc.), el UI muestra la caja
de chat lista pero el primer mensaje se pierde porque el backend no ha abierto stdin todavía.

**Fix:** esperar un evento de "listo" del backend, o implementar un pequeño buffer de mensajes
pendientes que se reintenten.

---

### 22. Slash commands definidos pero no implementados

**Archivo:** `cli-ui/src/components/ChatInput.tsx`

```typescript
const SLASH_COMMANDS = { "/init", "/plan", "/deploy", "/status", "/help" }
```

Están definidos como UI (con descripciones) pero en el backend `_stream_mode` se procesan como
mensajes de texto normales → "Recibido.". El usuario los ve en el UI y espera que funcionen.

---

## 🔵 DISEÑO — Problemas arquitectónicos

### 23. `os.environ` global contaminado con credenciales

**Archivo:** `src/aetherdeploy/cli.py`

Las credenciales se inyectan globalmente en `os.environ`. En cualquier escenario multi-sesión
futuro, todas las sesiones compartirían las mismas credenciales. Además, subprocess hijos
(Terraform, Docker) heredan automáticamente todas las variables, incluyendo tokens que no
deberían llegar a ciertos procesos.

---

### 24. `ContextVar` del emitter puede perder contexto async

**Archivo:** `src/aetherdeploy/agent/nodes/execution.py`

La inyección del emitter via `ContextVar` (`_emitter_var.set(emit)`) funciona dentro del mismo
contexto async. Sin embargo, LangGraph puede ejecutar nodos en contextos diferentes. El patrón
no está garantizado.

**Fix recomendado:** pasar el emitter como parámetro de configuración del grafo, no como
variable de contexto.

---

### 25. `MemorySaver` pierde estado al reiniciar el proceso

El grafo usa `MemorySaver` (in-memory). Si el backend Python se reinicia mientras hay un
despliegue en curso (error, ctrl-C), todo el estado se pierde. No hay forma de retomar una
sesión interrumpida.

---

### 26. `AWSProvider.verify_credentials()` tiene import dinámico de boto3

```python
def verify_credentials(self) -> bool:
    try:
        import boto3
        boto3.Session().client("sts").get_caller_identity()
        return True
    except Exception:
        return False
```

`boto3` puede no estar instalado. El `except Exception` captura `ImportError` y devuelve `False`
silenciosamente. El sistema pensaría que las credenciales son inválidas cuando en realidad
`boto3` no está instalado.

---

### 27. Errores de Terraform podrían filtrar credenciales

Si Terraform incluye valores de credenciales en su output de error (ocurre con algunos providers
mal configurados), el campo `stderr[:300]` podría capturar esos valores y enviarlos al frontend.
No hay sanitización de credenciales en los mensajes de error.

---

## Resumen de prioridades

| Prioridad | Issue | Impacto |
|-----------|-------|---------|
| 🔴 P0 | `emit` NameError en `execution_node` | El despliegue real falla siempre |
| 🔴 P0 | Credenciales fake aceptadas | Seguridad + UX completamente rota |
| 🔴 P0 | Ruta venv incorrecta | Backend no arranca con el venv correcto |
| 🔴 P0 | Sin manejo de excepciones en `run_graph` | Fallos silenciosos, UI confusa |
| 🔴 P0 | stderr invisible | Errores Python ocultos al usuario |
| 🔴 P0 | Exit code no verificado | Usuario no sabe que el backend murió |
| 🟠 P1 | Sin streaming de Terraform | UX congelada durante 10+ minutos |
| 🟠 P1 | `terraform plan` no ejecutado | Usuario aprueba "a ciegas" |
| 🟠 P1 | `discovery_needs_github` bloqueante | Sesión muerta si no hay proyecto local |
| 🟠 P1 | LLM nunca usado | La "IA" es en realidad decisiones hardcodeadas |
| 🟠 P1 | Sin recovery de estado `error` | Usuario atrapado sin salida |
| 🟡 P2 | y/n sin Enter activa inmediatamente | Confirmaciones accidentales en prod |
| 🟡 P2 | Sin validación de credenciales tras recibir | No hay confirmación de que funcionen |
| 🟡 P2 | Progreso ficticio en barra | Falsa expectativa de completado |
| 🟡 P2 | Slash commands sin implementar | Expectativas rotas |
| 🔵 P3 | `os.environ` global con credenciales | Riesgo de seguridad futuro |
| 🔵 P3 | `MemorySaver` efímero | No se puede reanudar una sesión |
| 🔵 P3 | Errores Terraform pueden filtrar secrets | Riesgo de seguridad |
