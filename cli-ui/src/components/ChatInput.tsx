import { Box, Text, useInput } from "ink"
import React, { useState } from "react"

const SLASH_COMMANDS: Record<string, string> = {
  "/init":   "Initialise the project in the current directory",
  "/plan":   "Generate the architecture plan",
  "/deploy": "Execute the deployment",
  "/status": "Show the deployment status",
  "/help":   "Show available commands",
}

interface Props {
  onSubmit: (value: string) => void
  waitingConfirmation: boolean
  onConfirm: (value: boolean) => void
  waitingPromotion: boolean
  onPromote: (value: boolean) => void
  disabled: boolean
}

export function ChatInput({ onSubmit, waitingConfirmation, onConfirm, waitingPromotion, onPromote, disabled }: Props) {
  const [value, setValue] = useState("")

  useInput((input, key) => {
    if (disabled) return

    if (key.return) {
      if (waitingPromotion) {
        const v = value.trim().toLowerCase()
        if (v === "y" || v === "yes") { onPromote(true); setValue("") }
        else if (v === "n" || v === "no") { onPromote(false); setValue("") }
        return
      }
      if (waitingConfirmation) {
        const v = value.trim().toLowerCase()
        if (v === "y" || v === "yes") { onConfirm(true); setValue("") }
        else if (v === "n" || v === "no") { onConfirm(false); setValue("") }
        return
      }
      const trimmed = value.trim()
      if (!trimmed) return
      onSubmit(trimmed)
      setValue("")
      return
    }

    if (key.backspace || key.delete) {
      setValue(v => v.slice(0, -1))
      return
    }

    if (!key.ctrl && !key.meta && input) {
      setValue(v => v + input)
    }
  })

  if (waitingPromotion) {
    return (
      <Box paddingX={1} flexDirection="column" gap={0}>
        <Box gap={1}>
          <Text color="magenta">Promote to real production?</Text>
          <Text color="gray"> (type y/n + Enter)</Text>
        </Box>
        <Box gap={1}>
          <Text color="cyan">▸ </Text>
          <Text color="white">{value}</Text>
          <Text color="white">█</Text>
        </Box>
      </Box>
    )
  }

  if (waitingConfirmation) {
    return (
      <Box paddingX={1} flexDirection="column" gap={0}>
        <Box gap={1}>
          <Text color="yellow">Do you want to proceed?</Text>
          <Text color="gray"> (type y/n + Enter)</Text>
        </Box>
        <Box gap={1}>
          <Text color="cyan">▸ </Text>
          <Text color="white">{value}</Text>
          <Text color="white">█</Text>
        </Box>
      </Box>
    )
  }

  return (
    <Box paddingX={1}>
      <Text color="cyan">▸ </Text>
      <Text color="white">{value}</Text>
      <Text color={disabled ? "gray" : "white"}>█</Text>
    </Box>
  )
}
