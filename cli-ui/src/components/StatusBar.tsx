import { Box, Text } from "ink"
import Spinner from "ink-spinner"
import React from "react"
import type { AgentState } from "../types.js"

const STATE_LABELS: Record<AgentState, string> = {
  idle:      "",
  analyzing: "Analysing project...",
  planning:  "Generating plan...",
  deploying: "Deploying...",
  error:     "An error occurred",
  success:   "Done!",
}

const SPINNING: AgentState[] = ["analyzing", "planning", "deploying"]

interface Props {
  state: AgentState
}

export function StatusBar({ state }: Props) {
  const label = STATE_LABELS[state]
  if (!label) return null

  const isSpinning = SPINNING.includes(state)

  return (
    <Box gap={1} paddingX={1}>
      {isSpinning && (
        <Text color="magenta">
          <Spinner type="dots" />
        </Text>
      )}
      <Text
        color={state === "error" ? "red" : state === "success" ? "green" : "gray"}
        dimColor={isSpinning}
      >
        {label}
      </Text>
    </Box>
  )
}
