import { Box, Text } from "ink"
import React from "react"

interface Props {
  percentage: number
  label: string
  width?: number
}

export function ProgressBar({ percentage, label, width = 36 }: Props) {
  const clamped = Math.max(0, Math.min(100, percentage))
  const filled = Math.round((clamped / 100) * width)
  const empty = width - filled

  return (
    <Box flexDirection="column" gap={0} paddingX={1}>
      <Box>
        <Text color="magenta">{"█".repeat(filled)}</Text>
        <Text color="gray" dimColor>{"░".repeat(empty)}</Text>
        <Text color="gray"> {clamped}%</Text>
      </Box>
      <Text color="gray" dimColor>{label}</Text>
    </Box>
  )
}
