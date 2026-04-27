import { type ChildProcess, spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { useCallback, useEffect, useRef, useState } from "react"
import type { AgentEvent, AgentState, ArchitectureProposal, Message, UserCommand } from "../types.js"

const __dirname = dirname(fileURLToPath(import.meta.url))

export interface AgentHook {
  agentState: AgentState
  messages: Message[]
  proposal: ArchitectureProposal | null
  deploymentResult: DeploymentResultData | null
  waitingConfirmation: boolean
  waitingPromotion: boolean
  waitingCredentials: boolean
  credentialFields: string[]
  credentialProvider: string
  credentialMessage: string
  promotionEndpoints: string[]
  progress: number | null
  ready: boolean
  llmProvider: string
  llmModel: string
  sendMessage: (content: string) => void
  confirm: (value: boolean) => void
  promote: (value: boolean) => void
  submitCredentials: (values: Record<string, string>) => void
  cancel: () => void
  retry: () => void
}

export interface DeploymentResultData {
  action: string
  endpoints: string[]
  environments: string[]
  provider?: string
}

export interface UseAgentOptions {
  projectPath?: string
  envs?: string[]
  dryRun?: boolean
}

// Find the Python backend binary
const BACKEND_MODULE = ["-m", "aetherdeploy", "stream"]
const MOCK_BACKEND = process.env["AETHER_MOCK_BACKEND"] === "true"
// cli-ui/src/hooks → ../../../  = aether-deploy root
const PROJECT_ROOT = join(__dirname, "../../..")
const DEFAULT_PYTHONPATH = join(PROJECT_ROOT, "src")
const DEFAULT_LOG_FILE = join(PROJECT_ROOT, ".aetherdeploy/logs/agent.jsonl")
const VENV_PYTHON = join(PROJECT_ROOT, ".venv/bin/python3")
const LEGACY_VENV_PYTHON = join(PROJECT_ROOT, "venv/bin/python3")
const DEFAULT_PYTHON = existsSync(VENV_PYTHON)
  ? VENV_PYTHON
  : existsSync(LEGACY_VENV_PYTHON)
    ? LEGACY_VENV_PYTHON
    : "python3"
const PYTHON_BIN = process.env["AETHER_PYTHON_BIN"] ?? DEFAULT_PYTHON

const d = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

const MOCK_PROPOSAL: ArchitectureProposal = {
  provider: "aws",
  region: "us-east-1",
  services: [
    { service: "ECS Fargate",  purpose: "compute",  justification: "Auto-scalable serverless containers",           cost: "$45" },
    { service: "CloudFront",   purpose: "cdn",      justification: "Global low-latency CDN",                         cost: "$12" },
    { service: "RDS Aurora",   purpose: "database", justification: "Managed PostgreSQL, high availability",          cost: "$65" },
  ],
  totalCost: "~$122",
  securityNotes: ["Minimum IAM for ECS tasks", "Secrets outside plain environment variables"],
  scalabilityNotes: ["Fargate scales horizontally by CPU/memory"],
  environments: [
    { name: "prod", strategy: "terraform" },
  ],
}

export function useAgent({ projectPath, envs, dryRun }: UseAgentOptions = {}): AgentHook {
  const [agentState, setAgentState] = useState<AgentState>("idle")
  const [messages, setMessages] = useState<Message[]>([])
  const [proposal, setProposal] = useState<ArchitectureProposal | null>(null)
  const [deploymentResult, setDeploymentResult] = useState<DeploymentResultData | null>(null)
  const [waitingConfirmation, setWaitingConfirmation] = useState(false)
  const [waitingPromotion, setWaitingPromotion] = useState(false)
  const [waitingCredentials, setWaitingCredentials] = useState(false)
  const [credentialFields, setCredentialFields] = useState<string[]>([])
  const [credentialProvider, setCredentialProvider] = useState("")
  const [credentialMessage, setCredentialMessage] = useState("")
  const [promotionEndpoints, setPromotionEndpoints] = useState<string[]>([])
  const [progress, setProgress] = useState<number | null>(null)
  const [ready, setReady] = useState(false)
  const [llmProvider, setLlmProvider] = useState("")
  const [llmModel, setLlmModel] = useState("")
  const [restartKey, setRestartKey] = useState(0)
  const procRef = useRef<ChildProcess | null>(null)
  const bufferRef = useRef("")
  const pendingRef = useRef<UserCommand[]>([])
  const readyRef = useRef(false)
  const mockAbortRef = useRef(false)

  function addMessage(msg: Message) {
    setMessages(prev => [...prev, msg])
  }

  // ---------------------------------------------------------------------------
  // Real backend: spawn Python process
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (MOCK_BACKEND) {
      readyRef.current = true
      setReady(true)
      return
    }

    readyRef.current = false
    setReady(false)
    const pythonPath = process.env["AETHER_PYTHONPATH"] ?? DEFAULT_PYTHONPATH
    const proc = spawn(PYTHON_BIN, BACKEND_MODULE, {
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        AETHER_LOG_FILE: process.env["AETHER_LOG_FILE"] ?? DEFAULT_LOG_FILE,
        AETHER_LLM_WAIT_FOREVER: process.env["AETHER_LLM_WAIT_FOREVER"] ?? "true",
      },
      stdio: ["pipe", "pipe", "pipe"],
    })
    procRef.current = proc

    proc.stdout?.on("data", (chunk: Buffer) => {
      bufferRef.current += chunk.toString()
      const lines = bufferRef.current.split("\n")
      bufferRef.current = lines.pop() ?? ""
      for (const line of lines) {
        if (!line.trim()) continue
        try {
          handleEvent(JSON.parse(line) as AgentEvent)
        } catch {
          // incomplete or non-JSON line, skip
        }
      }
    })

    proc.on("error", () => {
      addMessage({ role: "error", content: "Could not start the Python backend. Is aetherdeploy installed?" })
      setAgentState("error")
    })

    proc.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString().trim()
      if (text) {
        // Filter LangGraph/pydantic log lines that are not real errors
        const isNoise = text.includes("UserWarning") || text.includes("DeprecationWarning")
        if (!isNoise) {
          addMessage({ role: "error", content: `[Backend] ${text}` })
        }
      }
    })

    proc.on("exit", (code) => {
      if (code !== null && code !== 0) {
        addMessage({ role: "error", content: `Agent exited unexpectedly (code ${code}). Restart the application.` })
        setAgentState("error")
      } else {
        setAgentState("idle")
      }
    })

    return () => { proc.kill() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restartKey])

  function handleEvent(event: AgentEvent) {
    switch (event.type) {
      case "ready":
        readyRef.current = true
        setReady(true)
        if (event.llm_provider) setLlmProvider(event.llm_provider)
        if (event.llm_model) setLlmModel(event.llm_model)
        if (event.log_file) {
          addMessage({ role: "info", content: `Logs: ${event.log_file}` })
        }
        for (const cmd of pendingRef.current) writeCommand(cmd)
        pendingRef.current = []
        break
      case "state_change":
        setAgentState(event.state)
        if (event.message) addMessage({ role: "agent", content: event.message })
        break
      case "message":
        addMessage({ role: event.role === "user" ? "user" : "agent", content: event.content })
        break
      case "proposal":
        setProposal(event.data)
        break
      case "deployment_complete":
        setDeploymentResult({
          action: event.action,
          endpoints: event.endpoints,
          environments: event.environments,
          provider: event.provider,
        })
        break
      case "waiting_confirmation":
        setWaitingConfirmation(true)
        addMessage({ role: "agent", content: event.message })
        break
      case "waiting_promotion":
        setWaitingPromotion(true)
        setPromotionEndpoints(event.endpoints)
        addMessage({ role: "agent", content: event.message })
        break
      case "waiting_credentials":
        setWaitingCredentials(true)
        setCredentialFields(event.fields)
        setCredentialProvider(event.provider)
        setCredentialMessage(event.message)
        addMessage({ role: "agent", content: event.message })
        break
      case "progress":
        setProgress(event.percentage)
        break
      case "error":
        setAgentState("error")
        setProgress(null)
        addMessage({ role: "error", content: event.message })
        break
      case "done":
        setAgentState("success")
        setWaitingConfirmation(false)
        setWaitingPromotion(false)
        setWaitingCredentials(false)
        setProgress(null)
        break
    }
  }

  function send(cmd: UserCommand) {
    if (!readyRef.current && cmd.type !== "cancel") {
      pendingRef.current.push(cmd)
      return
    }
    writeCommand(cmd)
  }

  function writeCommand(cmd: UserCommand) {
    const proc = procRef.current
    if (!proc?.stdin?.writable) {
      addMessage({ role: "error", content: "Backend is not available. Use retry to start a new session." })
      setAgentState("error")
      return
    }
    proc.stdin.write(JSON.stringify(cmd) + "\n")
  }

  // ---------------------------------------------------------------------------
  // Mock backend: simulate a full deployment scenario
  // ---------------------------------------------------------------------------
  async function runMockFlow() {
    mockAbortRef.current = false
    const go = () => !mockAbortRef.current

    setAgentState("analyzing")
    setProgress(10)
    addMessage({ role: "agent", content: "Inspecting project directory..." })

    await d(1200); if (!go()) return
    setProgress(20)
    addMessage({ role: "agent", content: "Stack detected: Node.js 20 + Express · PostgreSQL 15" })

    await d(800); if (!go()) return
    addMessage({ role: "agent", content: "Dockerfile found · No docker-compose" })

    await d(600); if (!go()) return
    setAgentState("planning")
    setProgress(45)
    addMessage({ role: "agent", content: "Calculating optimal architecture for your workloads..." })

    await d(2000); if (!go()) return
    setProgress(55)
    setProposal(MOCK_PROPOSAL)
    setAgentState("idle")
    setWaitingConfirmation(true)
    addMessage({ role: "agent", content: "Do you want to proceed with this architecture proposal?" })
  }

  async function runMockDeploy() {
    mockAbortRef.current = false
    const go = () => !mockAbortRef.current

    setAgentState("deploying")

    const steps: Array<[number, number, string]> = [
      [800,  60, "Initialising Terraform workspace..."],
      [1200, 68, "Creating VPC and subnets in us-east-1..."],
      [1500, 75, "Launching ECS Fargate cluster..."],
      [1800, 82, "Configuring CloudFront distribution..."],
      [1500, 88, "Provisioning RDS Aurora Serverless..."],
      [1200, 94, "Binding IAM roles and security groups..."],
      [800,  100, "Infrastructure ready!"],
    ]

    for (const [ms, pct, msg] of steps) {
      await d(ms); if (!go()) return
      setProgress(pct)
      addMessage({ role: "agent", content: msg })
    }

    await d(500); if (!go()) return
    const mockEndpoints = ["https://d1abc4def.cloudfront.net"]
    addMessage({ role: "agent", content: `App available at: ${mockEndpoints[0]}` })
    setDeploymentResult({
      action: "deploy",
      endpoints: mockEndpoints,
      environments: ["prod"],
      provider: "aws",
    })
    setAgentState("success")
    setProgress(null)
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------
  const sendMessage = useCallback((content: string) => {
    addMessage({ role: "user", content })
    if (MOCK_BACKEND) {
      void runMockFlow()
      return
    }
    send({ type: "message", content, project_path: projectPath, environments: envs, dry_run: dryRun })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectPath, envs, dryRun])

  const confirm = useCallback((value: boolean) => {
    setWaitingConfirmation(false)
    addMessage({ role: "user", content: value ? "Yes, proceed" : "No, cancel" })
    if (MOCK_BACKEND) {
      if (value) void runMockDeploy()
      else setAgentState("idle")
      return
    }
    send({ type: "confirm", value })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const promote = useCallback((value: boolean) => {
    setWaitingPromotion(false)
    addMessage({ role: "user", content: value ? "Yes, promote to production" : "No, keep in feature" })
    if (!MOCK_BACKEND) send({ type: "promote", value })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const submitCredentials = useCallback((values: Record<string, string>) => {
    setWaitingCredentials(false)
    addMessage({ role: "info", content: "Credentials submitted." })
    if (!MOCK_BACKEND) send({ type: "credentials", values })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const cancel = useCallback(() => {
    mockAbortRef.current = true
    if (!MOCK_BACKEND) send({ type: "cancel" })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const retry = useCallback(() => {
    setAgentState("idle")
    setProgress(null)
    setProposal(null)
    setDeploymentResult(null)
    setWaitingConfirmation(false)
    setWaitingPromotion(false)
    setWaitingCredentials(false)
    setCredentialFields([])
    setCredentialProvider("")
    setCredentialMessage("")
    pendingRef.current = []
    readyRef.current = false
    procRef.current?.kill()
    procRef.current = null
    addMessage({ role: "info", content: "Restarting backend..." })
    setRestartKey(k => k + 1)
  }, [])

  return { agentState, messages, proposal, deploymentResult, waitingConfirmation, waitingPromotion, waitingCredentials, credentialFields, credentialProvider, credentialMessage, promotionEndpoints, progress, ready, llmProvider, llmModel, sendMessage, confirm, promote, submitCredentials, cancel, retry }
}
