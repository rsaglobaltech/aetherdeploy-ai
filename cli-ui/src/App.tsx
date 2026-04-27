import { Box, Text, useApp, useInput } from "ink"
import React, { useState } from "react"
import { AsciiLogo } from "./components/AsciiBanner.js"
import { ChatInput } from "./components/ChatInput.js"
import { CredentialsInput } from "./components/CredentialsInput.js"
import { DeploymentPanel } from "./components/DeploymentPanel.js"
import { Header } from "./components/Header.js"
import { MessageList } from "./components/MessageList.js"
import { ProgressBar } from "./components/ProgressBar.js"
import { ProposalPanel } from "./components/ProposalPanel.js"
import { StatusBar } from "./components/StatusBar.js"
import { useAgent } from "./hooks/useAgent.js"

const BUSY_STATES = ["analyzing", "planning", "deploying"] as const
const SHOW_BANNER = process.argv.includes("--full")

interface AppProps {
  projectPath?: string
  envs?: string[]
  dryRun?: boolean
}

// Estimated progress per state (visual — does not reflect real Terraform progress)
const STATE_PROGRESS: Record<string, number> = {
  analyzing: 20,
  planning:  55,
  deploying: 80,
}

const STATE_PROGRESS_LABEL: Record<string, string> = {
  analyzing: "Inspecting project...",
  planning:  "Generating architecture plan...",
  deploying: "Applying infrastructure...",
}

export function App({ projectPath, envs, dryRun }: AppProps = {}) {
  const { exit } = useApp()
  const {
    agentState,
    messages,
    proposal,
    deploymentResult,
    waitingConfirmation,
    waitingPromotion,
    waitingCredentials,
    credentialFields,
    credentialProvider,
    credentialMessage,
    progress: realProgress,
    ready,
    llmProvider,
    llmModel,
    sendMessage,
    confirm,
    promote,
    submitCredentials,
    cancel,
    retry,
  } = useAgent({ projectPath, envs, dryRun })

  const [scrollOffset, setScrollOffset] = useState(0)
  const isBusy = (BUSY_STATES as readonly string[]).includes(agentState)

  // Progress: use real backend value if available, otherwise fall back to state estimate.
  // Once we reach success/error/idle we stop showing the bar.
  const showProgress = agentState !== "error" && (isBusy || (realProgress !== null && agentState !== "idle"))
  const progress = realProgress ?? (isBusy && agentState !== "deploying" ? STATE_PROGRESS[agentState] : undefined)
  const progressLabel = STATE_PROGRESS_LABEL[agentState] ?? ""

  const visibleMessages = messages.slice(Math.max(0, messages.length - 12 + scrollOffset))

  useInput((input, key) => {
    if (key.escape) { cancel(); exit() }
    if (agentState === "error" && input.toLowerCase() === "r") retry()
    const maxBack = -Math.max(0, messages.length - 12)
    if (key.upArrow) setScrollOffset(o => Math.max(maxBack, o - 1))
    if (key.downArrow) setScrollOffset(o => Math.min(0, o + 1))
  })

  return (
    <Box flexDirection="column" gap={1} padding={1}>
      {SHOW_BANNER && <AsciiLogo />}

      <Header state={agentState} projectPath={projectPath} envs={envs} dryRun={dryRun} llmProvider={llmProvider} llmModel={llmModel} />

      {visibleMessages.length > 0 && (
        <MessageList messages={visibleMessages} />
      )}

      {messages.length > 12 && (
        <Box paddingX={2}>
          <Text dimColor color="gray">
            {scrollOffset < 0
              ? `↑↓ scroll (${-scrollOffset} message${-scrollOffset !== 1 ? "s" : ""} back)`
              : "↑ to see earlier messages"}
          </Text>
        </Box>
      )}

      {/* Show architecture proposal while waiting for the user's confirmation */}
      {proposal && waitingConfirmation && (
        <ProposalPanel proposal={proposal} />
      )}

      {/* After completion: show deployment result panel (deploy or plan) */}
      {agentState === "success" && deploymentResult && (
        <DeploymentPanel result={deploymentResult} />
      )}

      {showProgress && progress !== undefined && (
        <ProgressBar percentage={progress} label={progressLabel} />
      )}

      <StatusBar state={agentState} />

      {agentState === "error" && (
        <Box borderStyle="round" borderColor="red" paddingX={2} paddingY={1} flexDirection="column" gap={1}>
          <Text bold color="red">An error occurred</Text>
          <Text color="gray">Press r to restart the backend or Esc to exit.</Text>
        </Box>
      )}

      {waitingCredentials ? (
        <CredentialsInput
          provider={credentialProvider}
          message={credentialMessage}
          fields={credentialFields}
          onSubmit={submitCredentials}
          onCancel={() => { cancel(); exit() }}
        />
      ) : (
        <ChatInput
          onSubmit={sendMessage}
          waitingConfirmation={waitingConfirmation}
          onConfirm={confirm}
          waitingPromotion={waitingPromotion}
          onPromote={promote}
          disabled={isBusy || !ready || agentState === "error"}
        />
      )}
    </Box>
  )
}
