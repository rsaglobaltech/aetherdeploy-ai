import { Box, Text } from "ink"
import React from "react"

// ASCII art compacto — se muestra solo en modo --full o primera ejecución
const BANNER = `
  ___       __  __           ____             __
 / _ | ___ / /_/ /  ___ ____/ __ \\___  ____  / /__  __ __
/ __ |/ -_) __/ _ \\/ -_) __/ /_/ / -_)/ _ \\ / / _ \\/ // /
/_/ |_|\\__/\\__/_//_/\\__/_/  \\____/\\__// .__//_/\\___/\\_, /
                                     /_/           /___/`

interface Props {
  show?: boolean
}

export function AsciiLogo({ show = true }: Props) {
  if (!show) return null
  return (
    <Box paddingX={1}>
      <Text color="magenta" dimColor>
        {BANNER}
      </Text>
    </Box>
  )
}
