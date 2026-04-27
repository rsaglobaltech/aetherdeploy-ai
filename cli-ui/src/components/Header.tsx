import { Box, Text } from "ink"
import React from "react"
import { Mascot } from "./Mascot.js"
import type { AgentState } from "../types.js"

const VERSION = "0.0.1-beta"

interface Props {
  state: AgentState
  projectPath?: string
  envs?: string[]
  dryRun?: boolean
  llmProvider?: string
  llmModel?: string
}

const PROVIDER_LABEL: Record<string, string> = {
  ollama:    "Ollama",
  anthropic: "Anthropic",
  openai:    "OpenAI",
}

const PROVIDER_COLOR: Record<string, string> = {
  ollama:    "green",
  anthropic: "magenta",
  openai:    "cyan",
}

export function Header({ state, projectPath, envs, dryRun, llmProvider, llmModel }: Props) {
  const tags: string[] = []
  if (envs && envs.length > 0) tags.push(envs.join(", "))
  if (dryRun) tags.push("dry-run")

  return (
    <Box
      borderStyle="round"
      borderColor="magenta"
      paddingX={2}
      paddingY={1}
      gap={3}
    >
      {/* Animated mascot */}
      <Mascot state={state} />

      {/* Info column */}
      <Box flexDirection="column" justifyContent="center" gap={1}>
        <Box gap={2} alignItems="center">
          <Text bold color="white">AetherDeploy</Text>
          <Text color="magenta">v{VERSION}</Text>
          {tags.length > 0 && (
            <Text color="yellow">[{tags.join(" ")}]</Text>
          )}
        </Box>

        {(llmProvider || llmModel) && (
          <Box gap={1} alignItems="center">
            <Text color="gray">◆</Text>
            {llmProvider && (
              <Text bold color={PROVIDER_COLOR[llmProvider] ?? "white"}>
                {PROVIDER_LABEL[llmProvider] ?? llmProvider}
              </Text>
            )}
            {llmProvider && llmModel && <Text color="gray">·</Text>}
            {llmModel && <Text color="white">{llmModel}</Text>}
          </Box>
        )}

        <Box>
          {projectPath
            ? <Text color="gray">Project: <Text color="cyan">{projectPath}</Text></Text>
            : <Text color="gray">Describe your deployment to get started.</Text>
          }
        </Box>

        <Box>
          <Text dimColor>
            <Text color="cyan">/init</Text>
            <Text color="gray"> analyse · </Text>
            <Text color="cyan">/plan</Text>
            <Text color="gray"> plan · </Text>
            <Text color="cyan">/deploy</Text>
            <Text color="gray"> deploy · </Text>
            <Text color="cyan">/destroy</Text>
            <Text color="gray"> destroy</Text>
          </Text>
        </Box>

        <Box>
          <Text dimColor color="gray">AetherDeploy uses AI — always review the plan before applying.</Text>
        </Box>
      </Box>
    </Box>
  )
}
