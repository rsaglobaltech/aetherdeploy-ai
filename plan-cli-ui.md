# Plan CLI UI — AetherDeploy Copilot Experience

## Contexto

AetherDeploy necesita una experiencia de terminal visual de nivel producto — bordes redondeados, mascota animada, estados visuales, feedback en tiempo real — inspirada en GitHub Copilot CLI pero con identidad propia.

El backend agéntico ya está planificado en Python/LangGraph (`plan.md`). Este plan cubre exclusivamente la **capa de presentación** en Node.js + Ink (React para terminal).

---

## Arquitectura: Dos Capas Desacopladas

```
┌─────────────────────────────────────────────┐
│  CLI UI Layer (Node.js + Ink)               │
│  • Renderizado React en terminal            │
│  • Mascota animada por estado               │
│  • Input conversacional                     │
│  • Bordes, colores, layout Copilot          │
└────────────┬────────────────────────────────┘
             │  JSON-lines sobre stdio (IPC)
             │  Ink spawna Python como child process
             │  Python emite eventos estructurados
┌────────────▼────────────────────────────────┐
│  Agent Backend (Python + LangGraph)         │
│  • LLM (Ollama/Gemma3)                      │
│  • Análisis de proyectos                    │
│  • Propuesta de arquitectura                │
│  • Generación Terraform                     │
│  • Ejecución de despliegue                  │
└─────────────────────────────────────────────┘
```

**Por qué esta separación**: Ink y Python son herramientas excelentes en sus dominios respectivos. Acoplarlos significaría sacrificar la experiencia visual (si todo fuera Python) o el ecosistema de IA/cloud (si todo fuera Node). El protocolo de comunicación es simple y bien definido: JSON-lines por stdio.

**Por qué no Rich (Python)**: Rich puede hacer paneles y spinners, pero no tiene el modelo de componentes reactivos de React/Ink. Para animaciones con estado complejo y layout Copilot-level, Ink es significativamente más potente.

---

## Protocolo de Comunicación (IPC)

Ink spawna el proceso Python con `--stream-json` y lee líneas de stdout. Cada línea es un objeto JSON:

```typescript
// Eventos que Python emite → Ink consume
type AgentEvent =
  | { type: "state_change"; state: AgentState; message: string }
  | { type: "analysis_result"; data: ProjectAnalysis }
  | { type: "proposal"; data: ArchitectureProposal }
  | { type: "waiting_confirmation"; message: string; options: string[] }
  | { type: "progress"; step: string; percentage: number }
  | { type: "log"; level: "info" | "warn" | "error"; message: string }
  | { type: "deployment_result"; data: DeploymentResult }
  | { type: "error"; message: string; recoverable: boolean }
  | { type: "done" }

// Estados del agente (controlan la animación)
type AgentState = "idle" | "analyzing" | "planning" | "deploying" | "error" | "success"

// Comandos que Ink envía → Python stdin
type UserCommand =
  | { type: "message"; content: string }
  | { type: "confirm"; value: boolean }
  | { type: "cancel" }
```

Python lee stdin en modo streaming en el mismo loop. Ink escribe en stdin del proceso hijo.

---

## Estructura de Directorios

```
aether-deploy/
└── cli-ui/
    ├── package.json
    ├── tsconfig.json
    ├── bin/
    │   └── aetherdeploy.js       # Entry point: #!/usr/bin/env node
    └── src/
        ├── index.tsx             # Render(<App />) con Ink
        ├── App.tsx               # Raíz: orquesta layout y estado global
        │
        ├── components/
        │   ├── Header.tsx        # Panel superior con mascota + título
        │   ├── Mascot.tsx        # Icono animado (frames por estado)
        │   ├── MessageList.tsx   # Historial de conversación
        │   ├── StatusBar.tsx     # Estado actual + spinner
        │   ├── ChatInput.tsx     # Input del usuario con prompt
        │   ├── ProposalPanel.tsx # Tabla de arquitectura propuesta
        │   └── ProgressBar.tsx   # Barra de progreso para deploys
        │
        ├── hooks/
        │   ├── useAgent.ts       # Comunicación con Python via stdio
        │   └── useAnimation.ts   # Máquina de estados de la mascota
        │
        └── constants/
            ├── mascot-frames.ts  # Frames ASCII de animación por estado
            └── theme.ts          # Colores, gradientes, estilos
```

---

## FASE UI-1: Setup y Layout Base ✅ COMPLETADA

**Objetivo**: `aetherdeploy` muestra el panel Copilot estático con mascota en idle, input funcional, y comunicación básica con el backend Python.

**Criterio de verificación**: El comando renderiza el panel con bordes redondeados, mascota animada en idle, y el usuario puede escribir texto que se envía al proceso Python.

### Ficheros a crear

#### `cli-ui/package.json`

```json
{
  "name": "@aetherdeploy/cli-ui",
  "version": "1.0.0",
  "bin": { "aetherdeploy": "./bin/aetherdeploy.js" },
  "scripts": {
    "dev": "tsx watch src/index.tsx",
    "build": "tsc",
    "start": "node bin/aetherdeploy.js"
  },
  "dependencies": {
    "ink": "^5.0.0",
    "ink-spinner": "^5.0.0",
    "react": "^18.0.0",
    "chalk": "^5.3.0",
    "ink-gradient": "^3.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.0.0",
    "tsx": "^4.0.0",
    "typescript": "^5.0.0"
  }
}
```

#### `cli-ui/src/constants/mascot-frames.ts`

```typescript
// Frames por estado del agente
export const MASCOT_FRAMES: Record<AgentState, string[]> = {
  idle: [
    "☁️  (•_•)",
    "☁️  (•‿•)",
    "☁️  (-_-)",
    "☁️  (•_•)",
  ],
  analyzing: [
    "🔍 (•_•)  ",
    "🔍  (•_•) ",
    "🔍   (•_•)",
    "🔍  (•_•) ",
  ],
  planning: [
    "⚙️  (•_•)",
    "⚙️  (°_°)",
    "⚙️  (•_•)",
    "⚙️  (ō_ō)",
  ],
  deploying: [
    "🚀 (•̀ᴗ•́)",
    "🚀  (•̀ᴗ•́)",
    "🚀   (•̀ᴗ•́)",
    "🚀  (•̀ᴗ•́)",
  ],
  error: [
    "💥 (>_<)",
    "💥 (x_x)",
    "💥 (>_<)",
  ],
  success: [
    "✅ (^‿^)",
    "🎉 (^‿^)",
    "✅ (^‿^)",
  ],
}

export const FRAME_INTERVAL_MS = 600
```

#### `cli-ui/src/constants/theme.ts`

```typescript
import chalk from "chalk"

export const theme = {
  // Colores principales (dark mode / neón)
  primary:   chalk.hex("#7C3AED"),   // violeta AetherDeploy
  accent:    chalk.hex("#06B6D4"),   // cyan neón
  success:   chalk.hex("#10B981"),   // verde
  warning:   chalk.hex("#F59E0B"),   // ámbar
  error:     chalk.hex("#EF4444"),   // rojo
  muted:     chalk.hex("#6B7280"),   // gris
  text:      chalk.hex("#F9FAFB"),   // blanco suave

  // Bordes del panel (Unicode box-drawing)
  border: {
    topLeft:     "╭",
    topRight:    "╮",
    bottomLeft:  "╰",
    bottomRight: "╯",
    horizontal:  "─",
    vertical:    "│",
  },

  // Prefijos de mensajes
  prefix: {
    user:  chalk.hex("#06B6D4")("▸"),
    agent: chalk.hex("#7C3AED")("◈"),
    info:  chalk.hex("#6B7280")("·"),
    error: chalk.hex("#EF4444")("✗"),
  },
}
```

#### `cli-ui/src/hooks/useAnimation.ts`

```typescript
import { useState, useEffect } from "react"
import { MASCOT_FRAMES, FRAME_INTERVAL_MS } from "../constants/mascot-frames"

export function useAnimation(state: AgentState) {
  const [frameIndex, setFrameIndex] = useState(0)
  const frames = MASCOT_FRAMES[state]

  useEffect(() => {
    setFrameIndex(0)  // reset al cambiar de estado
    const interval = setInterval(() => {
      setFrameIndex(i => (i + 1) % frames.length)
    }, FRAME_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [state, frames.length])

  return frames[frameIndex]
}
```

#### `cli-ui/src/hooks/useAgent.ts`

```typescript
import { useState, useEffect, useCallback } from "react"
import { spawn, ChildProcess } from "child_process"

export function useAgent() {
  const [agentState, setAgentState] = useState<AgentState>("idle")
  const [messages, setMessages] = useState<Message[]>([])
  const [proposal, setProposal] = useState<ArchitectureProposal | null>(null)
  const [waitingConfirmation, setWaitingConfirmation] = useState(false)
  const [proc, setProc] = useState<ChildProcess | null>(null)

  useEffect(() => {
    // Spawna el backend Python con --stream-json
    const p = spawn("python", ["-m", "aetherdeploy", "--stream-json"], {
      env: { ...process.env }
    })

    p.stdout?.on("data", (chunk: Buffer) => {
      // JSON-lines: cada línea es un evento
      chunk.toString().split("\n").filter(Boolean).forEach(line => {
        try {
          const event: AgentEvent = JSON.parse(line)
          handleEvent(event)
        } catch { /* línea incompleta, ignorar */ }
      })
    })

    setProc(p)
    return () => p.kill()
  }, [])

  const handleEvent = useCallback((event: AgentEvent) => {
    switch (event.type) {
      case "state_change":
        setAgentState(event.state)
        addMessage({ role: "agent", content: event.message })
        break
      case "proposal":
        setProposal(event.data)
        break
      case "waiting_confirmation":
        setWaitingConfirmation(true)
        addMessage({ role: "agent", content: event.message })
        break
      case "error":
        setAgentState("error")
        addMessage({ role: "error", content: event.message })
        break
      case "done":
        setAgentState("success")
        break
    }
  }, [])

  const sendMessage = useCallback((content: string) => {
    if (!proc) return
    addMessage({ role: "user", content })
    proc.stdin?.write(JSON.stringify({ type: "message", content }) + "\n")
  }, [proc])

  const confirm = useCallback((value: boolean) => {
    if (!proc) return
    setWaitingConfirmation(false)
    proc.stdin?.write(JSON.stringify({ type: "confirm", value }) + "\n")
  }, [proc])

  return { agentState, messages, proposal, waitingConfirmation, sendMessage, confirm }
}
```

#### `cli-ui/src/components/Mascot.tsx`

```tsx
import React from "react"
import { Text } from "ink"
import { useAnimation } from "../hooks/useAnimation"
import { theme } from "../constants/theme"

interface Props {
  state: AgentState
}

export function Mascot({ state }: Props) {
  const frame = useAnimation(state)
  return <Text color={theme.accent.toString()}>{frame}</Text>
}
```

#### `cli-ui/src/components/Header.tsx`

```tsx
import React from "react"
import { Box, Text } from "ink"
import { Mascot } from "./Mascot"
import { theme } from "../constants/theme"

interface Props {
  state: AgentState
  version: string
}

export function Header({ state, version }: Props) {
  return (
    <Box
      borderStyle="round"        // bordes redondeados ╭─╮
      borderColor="magenta"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
      width={54}
    >
      <Box gap={2}>
        <Mascot state={state} />
        <Text bold color="white">
          AetherDeploy{" "}
          <Text color="gray">v{version}</Text>
        </Text>
      </Box>

      <Box marginTop={1}>
        <Text color="gray">Describe tu despliegue para comenzar.</Text>
      </Box>

      <Box marginTop={1}>
        <Text color="gray" dimColor>
          Tip: <Text color="cyan">/init</Text> inicializa el proyecto ·{" "}
          <Text color="cyan">/plan</Text> genera el plan
        </Text>
      </Box>

      <Box marginTop={1}>
        <Text color="gray" dimColor>AetherDeploy usa IA. Revisa el plan antes de ejecutar.</Text>
      </Box>
    </Box>
  )
}
```

#### `cli-ui/src/components/ChatInput.tsx`

```tsx
import React, { useState } from "react"
import { Box, Text, useInput } from "ink"
import { theme } from "../constants/theme"

interface Props {
  onSubmit: (value: string) => void
  waitingConfirmation: boolean
  onConfirm: (value: boolean) => void
  disabled: boolean
}

export function ChatInput({ onSubmit, waitingConfirmation, onConfirm, disabled }: Props) {
  const [value, setValue] = useState("")

  useInput((input, key) => {
    if (disabled) return

    if (waitingConfirmation) {
      if (input === "y" || input === "Y") onConfirm(true)
      if (input === "n" || input === "N") onConfirm(false)
      return
    }

    if (key.return) {
      if (value.trim()) {
        onSubmit(value.trim())
        setValue("")
      }
      return
    }

    if (key.backspace || key.delete) {
      setValue(v => v.slice(0, -1))
      return
    }

    if (!key.ctrl && !key.meta) {
      setValue(v => v + input)
    }
  })

  if (waitingConfirmation) {
    return (
      <Box>
        <Text color="yellow">¿Deseas continuar? </Text>
        <Text color="gray">(y/n) </Text>
      </Box>
    )
  }

  return (
    <Box>
      <Text color="cyan">▸ </Text>
      <Text>{value}</Text>
      <Text color="gray">█</Text>
    </Box>
  )
}
```

#### `cli-ui/src/components/MessageList.tsx`

```tsx
import React from "react"
import { Box, Text } from "ink"

interface Message {
  role: "user" | "agent" | "error" | "info"
  content: string
}

export function MessageList({ messages }: { messages: Message[] }) {
  return (
    <Box flexDirection="column" gap={0}>
      {messages.map((msg, i) => (
        <Box key={i} gap={1}>
          <Text color={msg.role === "user" ? "cyan" : msg.role === "error" ? "red" : "magenta"}>
            {msg.role === "user" ? "▸" : "◈"}
          </Text>
          <Text color={msg.role === "error" ? "red" : "white"}>{msg.content}</Text>
        </Box>
      ))}
    </Box>
  )
}
```

#### `cli-ui/src/components/StatusBar.tsx`

```tsx
import React from "react"
import { Box, Text } from "ink"
import Spinner from "ink-spinner"

const STATE_LABELS: Record<AgentState, string> = {
  idle:      "Listo",
  analyzing: "Analizando proyecto...",
  planning:  "Generando plan...",
  deploying: "Desplegando...",
  error:     "Error",
  success:   "Completado",
}

const SPINNING_STATES: AgentState[] = ["analyzing", "planning", "deploying"]

export function StatusBar({ state }: { state: AgentState }) {
  const isSpinning = SPINNING_STATES.includes(state)
  const label = STATE_LABELS[state]

  if (state === "idle") return null

  return (
    <Box gap={1} marginTop={1}>
      {isSpinning && <Spinner type="dots" />}
      <Text color={state === "error" ? "red" : state === "success" ? "green" : "gray"}>
        {label}
      </Text>
    </Box>
  )
}
```

#### `cli-ui/src/App.tsx`

```tsx
import React from "react"
import { Box, useApp } from "ink"
import { Header } from "./components/Header"
import { MessageList } from "./components/MessageList"
import { StatusBar } from "./components/StatusBar"
import { ChatInput } from "./components/ChatInput"
import { useAgent } from "./hooks/useAgent"

const VERSION = "1.0.0"

export function App() {
  const { exit } = useApp()
  const { agentState, messages, waitingConfirmation, sendMessage, confirm } = useAgent()

  const isIdle = agentState === "idle"

  return (
    <Box flexDirection="column" gap={1} padding={1}>
      <Header state={agentState} version={VERSION} />
      
      {messages.length > 0 && (
        <Box flexDirection="column" paddingX={1}>
          <MessageList messages={messages} />
        </Box>
      )}

      <StatusBar state={agentState} />

      <Box paddingX={1}>
        <ChatInput
          onSubmit={sendMessage}
          waitingConfirmation={waitingConfirmation}
          onConfirm={confirm}
          disabled={agentState === "deploying" || agentState === "analyzing" || agentState === "planning"}
        />
      </Box>
    </Box>
  )
}
```

#### `cli-ui/src/index.tsx`

```tsx
import React from "react"
import { render } from "ink"
import { App } from "./App"

render(<App />)
```

#### `cli-ui/bin/aetherdeploy.js`

```js
#!/usr/bin/env node
import "../src/index.tsx"
```

---

## FASE UI-2: Panel de Propuesta y Comandos ✅ COMPLETADA (ProposalPanel incluido en UI-1)

**Objetivo**: Cuando el agente genera una `ArchitectureProposal`, el CLI la renderiza como tabla formateada con costes, justificaciones y servicios. Los comandos `/init`, `/plan`, `/deploy` funcionan.

**Criterio de verificación**: Al escribir `/plan`, el CLI muestra la propuesta en una tabla con bordes. El usuario puede aprobar con `y` y el agente continúa.

### Ficheros a crear

#### `cli-ui/src/components/ProposalPanel.tsx`

```tsx
import React from "react"
import { Box, Text } from "ink"

interface ServiceRow {
  service: string
  purpose: string
  justification: string
  cost: string
}

interface Props {
  proposal: {
    provider: string
    region: string
    services: ServiceRow[]
    totalCost: string
  }
}

export function ProposalPanel({ proposal }: Props) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor="magenta" paddingX={2} paddingY={1}>
      <Box gap={2}>
        <Text bold color="white">Propuesta: </Text>
        <Text color="cyan">{proposal.provider.toUpperCase()}</Text>
        <Text color="gray">({proposal.region})</Text>
      </Box>

      <Box flexDirection="column" marginTop={1} gap={0}>
        {proposal.services.map((svc, i) => (
          <Box key={i} gap={2}>
            <Text color="magenta">◈</Text>
            <Text bold color="white" width={20}>{svc.service}</Text>
            <Text color="gray" width={12}>{svc.purpose}</Text>
            <Text color="green">{svc.cost}/mes</Text>
          </Box>
        ))}
      </Box>

      <Box marginTop={1} borderTop borderColor="gray">
        <Text color="gray">Total estimado: </Text>
        <Text bold color="green">{proposal.totalCost}/mes</Text>
      </Box>
    </Box>
  )
}
```

#### Manejo de comandos en `useAgent.ts`

```typescript
const COMMANDS: Record<string, string> = {
  "/init":   "Inicializar proyecto en el directorio actual",
  "/plan":   "Generar plan de arquitectura",
  "/deploy": "Ejecutar despliegue",
  "/status": "Ver estado del despliegue actual",
  "/help":   "Mostrar comandos disponibles",
}

// En sendMessage: detectar comandos y formatear el envío
const sendMessage = (content: string) => {
  const isCommand = content.startsWith("/")
  if (isCommand && COMMANDS[content] === undefined) {
    addMessage({ role: "error", content: `Comando desconocido: ${content}` })
    return
  }
  // ... enviar al backend
}
```

---

## FASE UI-3: Animaciones Avanzadas y Pulido ✅ COMPLETADA

**Objetivo**: Experiencia visual pulida nivel producto. Gradiente en el título, transiciones suaves entre estados, barra de progreso para deploys largos, historial scrollable.

**Criterio de verificación**: Un deploy completo de principio a fin se ve fluido. Las transiciones entre estados son suaves. El historial funciona con texto largo.

### Mejoras a implementar

#### Gradiente en el header

```tsx
import Gradient from "ink-gradient"

// En Header.tsx
<Gradient name="cristal">
  <Text bold>AetherDeploy</Text>
</Gradient>
```

#### `cli-ui/src/components/ProgressBar.tsx`

```tsx
import React from "react"
import { Box, Text } from "ink"

interface Props {
  percentage: number   // 0-100
  label: string
}

export function ProgressBar({ percentage, label }: Props) {
  const width = 40
  const filled = Math.round((percentage / 100) * width)
  const empty = width - filled

  return (
    <Box flexDirection="column" gap={0}>
      <Box>
        <Text color="magenta">{"█".repeat(filled)}</Text>
        <Text color="gray">{"░".repeat(empty)}</Text>
        <Text color="gray"> {percentage}%</Text>
      </Box>
      <Text color="gray" dimColor>{label}</Text>
    </Box>
  )
}
```

#### Historial scrollable

```tsx
// En App.tsx: usar useInput para scroll
const [scrollOffset, setScrollOffset] = useState(0)
useInput((_, key) => {
  if (key.upArrow) setScrollOffset(o => Math.max(0, o - 1))
  if (key.downArrow) setScrollOffset(o => o + 1)
})
```

#### ASCII art de bienvenida (opcional, modo `--full`)

```
    ___       __  __             ____             __
   /   | ____/ /_/ /_  ___  ___|  _ \___  ____  / /___  __  __
  / /| |/ __  / __/ _ \/ __|  | | / _ \/ __ \/ / / _ \/ / / /
 / ___ / /_/ / /_/  __/ /  |  |_/ /  __/ /_/ / / / __/ /_/ /
/_/  |_\____/\__/\___/_/   |____/\___/ .___/_/_/\___/\__, /
                                     /_/           /____/
```

---

## Integración con el Backend Python

Para que el backend Python emita eventos JSON-lines, se añade a `src/aetherdeploy/cli.py` el flag `--stream-json`:

```python
@app.command()
def run_stream(
    stream_json: bool = typer.Option(False, "--stream-json", hidden=True)
):
    """Modo stream para el CLI UI de Ink."""
    if stream_json:
        asyncio.run(_run_stream_mode())

async def _run_stream_mode():
    """Lee comandos de stdin, emite eventos JSON a stdout."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid4())}}
    
    async def emit(event: dict):
        print(json.dumps(event), flush=True)  # JSON-lines
    
    # Loop: leer stdin línea a línea
    async for line in _stdin_lines():
        cmd = json.loads(line)
        if cmd["type"] == "message":
            await emit({"type": "state_change", "state": "analyzing", "message": "Analizando proyecto..."})
            async for graph_event in graph.astream({"user_message": cmd["content"]}, config):
                await _handle_graph_event(graph_event, emit)
        elif cmd["type"] == "confirm":
            graph.update_state(config, {"user_approved": cmd["value"]})
            async for graph_event in graph.astream(None, config):
                await _handle_graph_event(graph_event, emit)
```

---

## Árbol de Dependencias (CLI UI)

```
UI-1: Setup, layout base, mascota idle, input, IPC con Python
    └── UI-2: Panel de propuesta, comandos /slash, confirmación visual
            └── UI-3: Gradientes, progress bar, scroll, ASCII art
```

---

## Decisiones de Diseño

| Decisión | Elección | Razón |
|---|---|---|
| Framework CLI | Ink (React) | Modelo de componentes reactivos, mejor para UI con estado complejo |
| IPC | JSON-lines sobre stdio | Simple, sin puerto, sin servidor — Ink spawna Python como child process |
| Animación | `setInterval` con frames | Fácil de controlar por estado, loop cancelable con cleanup |
| Bordes | `borderStyle="round"` de Ink | Nativo, sin dependencias extra, soporte unicode garantizado |
| Colores | chalk + hex | Colores exactos en hex para brand consistency |
| Gradiente | ink-gradient | Única dependencia extra, resultado visual muy destacado |

---

## Verificación

```bash
# Instalar dependencias
cd cli-ui && npm install

# Modo desarrollo (hot reload)
npm run dev

# Verificar que spawna el backend correctamente
node bin/aetherdeploy.js

# Verificar layout con Python mockeado
AETHER_MOCK_BACKEND=true node bin/aetherdeploy.js
```

**Flujo visual esperado**:

```
╭──────────────────────────────────────────────────────╮
│  ☁️  (•_•)  AetherDeploy v1.0.0                     │
│                                                      │
│  Describe tu despliegue para comenzar.               │
│                                                      │
│  Tip: /init inicializa el proyecto · /plan           │
│                                                      │
│  AetherDeploy usa IA. Revisa el plan antes de        │
│  ejecutar.                                           │
╰──────────────────────────────────────────────────────╯

▸ Quiero desplegar mi app

◈ Analizando proyecto...
⠋ Analizando proyecto...

◈ Stack detectado: Node.js + Express
⠋ Generando plan...

╭──────────────────────────────────────────────────────╮
│  Propuesta: AWS (us-east-1)                          │
│                                                      │
│  ◈ ECS Fargate      compute      $45/mes             │
│  ◈ CloudFront       cdn          $12/mes             │
│  ◈ RDS Aurora       database     $65/mes             │
│                                                      │
│  Total estimado: ~$122/mes                           │
╰──────────────────────────────────────────────────────╯

¿Deseas continuar? (y/n) █

🚀 (•̀ᴗ•́) Desplegando...
████████████████░░░░░░░░░░░░░░░░░░░░░░░░ 45%
Creando cluster ECS...
```
