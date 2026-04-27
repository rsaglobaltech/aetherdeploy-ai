import type { AgentState } from "./constants/mascot-frames.js"

export type { AgentState }

export interface Message {
  role: "user" | "agent" | "error" | "info"
  content: string
}

export interface ServiceInfo {
  service: string
  purpose: string
  justification: string
  cost: string
}

export interface ArchitectureProposal {
  provider: string
  region: string
  services: ServiceInfo[]
  totalCost: string
  securityNotes?: string[]
  scalabilityNotes?: string[]
  environments?: Array<{ name: string; strategy: string }>
}

// Eventos que el backend Python emite via stdout (JSON-lines)
export type AgentEvent =
  | { type: "ready"; log_file?: string; llm_provider?: string; llm_model?: string }
  | { type: "state_change"; state: AgentState; step: string; message?: string }
  | { type: "message"; role: string; content: string }
  | { type: "proposal"; data: ArchitectureProposal }
  | { type: "waiting_confirmation"; message: string }
  | { type: "waiting_promotion"; message: string; endpoints: string[] }
  | { type: "waiting_credentials"; provider: string; message: string; fields: string[] }
  | { type: "progress"; step: string; percentage: number }
  | { type: "error"; message: string; recoverable: boolean }
  | { type: "deployment_complete"; action: string; endpoints: string[]; environments: string[]; provider?: string }
  | { type: "done" }

// Comandos que el frontend envía via stdin
export type UserCommand =
  | { type: "message"; content: string; project_path?: string; environments?: string[]; dry_run?: boolean }
  | { type: "confirm"; value: boolean }
  | { type: "promote"; value: boolean }
  | { type: "credentials"; values: Record<string, string> }
  | { type: "cancel" }
