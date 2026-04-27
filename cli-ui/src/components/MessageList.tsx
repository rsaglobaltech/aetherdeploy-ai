import { Box, Text } from "ink"
import React from "react"
import type { Message } from "../types.js"

interface Props {
  messages: Message[]
}

const ROLE_STYLE: Record<Message["role"], { prefix: string; color: string }> = {
  user:  { prefix: "▸", color: "cyan" },
  agent: { prefix: "◈", color: "magenta" },
  error: { prefix: "✗", color: "red" },
  info:  { prefix: "·", color: "gray" },
}

export function MessageList({ messages }: Props) {
  if (messages.length === 0) return null

  return (
    <Box flexDirection="column" paddingX={1} gap={0}>
      {messages.map((msg, i) => {
        const style = ROLE_STYLE[msg.role]
        return (
          <Box key={i} gap={1}>
            <Text color={style.color}>{style.prefix}</Text>
            <Text color={msg.role === "error" ? "red" : msg.role === "user" ? "white" : "gray"}>
              {msg.content}
            </Text>
          </Box>
        )
      })}
    </Box>
  )
}
