import { Box, Text } from "ink"
import React from "react"
import { useAnimation } from "../hooks/useAnimation.js"
import type { AgentState } from "../types.js"

interface Props {
  state: AgentState
}

export function Mascot({ state }: Props) {
  const frame = useAnimation(state)
  return (
    <Box flexDirection="column" width={11} flexShrink={0}>
      {frame.split("\n").map((line, i) => (
        <Text key={i} color="cyan">{line}</Text>
      ))}
    </Box>
  )
}
