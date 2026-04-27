import { Box, Text, useInput } from "ink"
import React, { useState } from "react"

const SENSITIVE_PATTERN = /SECRET|KEY|TOKEN|PASSWORD/i
const AWS_ACCESS_KEY_RE = /^A(KIA|SIA)[0-9A-Z]{16}$/
const AWS_SECRET_RE = /^[A-Za-z0-9/+=]{40}$/

interface Props {
  provider: string
  message: string
  fields: string[]
  onSubmit: (values: Record<string, string>) => void
  onCancel: () => void
}

export function CredentialsInput({ provider, message, fields, onSubmit, onCancel }: Props) {
  const [fieldIndex, setFieldIndex] = useState(0)
  const [values, setValues] = useState<Record<string, string>>({})
  const [current, setCurrent] = useState("")
  const [fieldError, setFieldError] = useState("")

  const currentField = fields[fieldIndex]!
  const isSensitive = SENSITIVE_PATTERN.test(currentField)
  const displayValue = isSensitive ? "*".repeat(current.length) : current

  useInput((input, key) => {
    if (key.escape) {
      onCancel()
      return
    }

    if (key.return) {
      const trimmed = current.trim()
      const validationError = validateField(currentField, trimmed)
      if (validationError) {
        setFieldError(validationError)
        return
      }
      setFieldError("")
      const updated = { ...values, [currentField]: trimmed }
      setValues(updated)
      setCurrent("")

      if (fieldIndex < fields.length - 1) {
        setFieldIndex(i => i + 1)
      } else {
        onSubmit(updated)
      }
      return
    }

    if (key.backspace || key.delete) {
      setCurrent(v => v.slice(0, -1))
      setFieldError("")
      return
    }

    if (!key.ctrl && !key.meta && input) {
      setCurrent(v => v + input)
    }
  })

  const providerLabel: Record<string, string> = {
    localstack: "LocalStack",
    aws: "AWS",
    gcp: "GCP",
    azure: "Azure",
  }

  return (
    <Box flexDirection="column" gap={1} paddingX={1}>
      <Box
        borderStyle="round"
        borderColor="yellow"
        paddingX={2}
        paddingY={1}
        flexDirection="column"
        gap={1}
      >
        <Text bold color="yellow">
          🔑 Credentials required — {providerLabel[provider] ?? provider}
        </Text>
        <Text color="gray">{message}</Text>
      </Box>

      <Box gap={1} flexDirection="column" paddingX={1}>
        {fields.map((field, i) => {
          if (i < fieldIndex) {
            return (
              <Box key={field} gap={1}>
                <Text color="green">✓</Text>
                <Text color="gray">{field}</Text>
              </Box>
            )
          }
          if (i === fieldIndex) {
            return (
              <Box key={field} gap={1} flexDirection="column">
                <Text color="cyan">{field}</Text>
                <Box gap={1}>
                  <Text color="cyan">▸ </Text>
                  <Text color="white">{displayValue}</Text>
                  <Text color="white">█</Text>
                </Box>
                {fieldError && <Text color="red">{fieldError}</Text>}
              </Box>
            )
          }
          return (
            <Box key={field} gap={1}>
              <Text color="gray" dimColor>· {field}</Text>
            </Box>
          )
        })}
      </Box>

      <Box paddingX={1}>
        <Text dimColor color="gray">
          Enter to confirm · Esc to cancel
          {fields.length > 1 ? ` · ${fieldIndex + 1}/${fields.length}` : ""}
        </Text>
      </Box>
    </Box>
  )
}

function validateField(field: string, value: string): string {
  if (!value) return "This field is required"
  if (field === "AWS_ACCESS_KEY_ID" && !AWS_ACCESS_KEY_RE.test(value)) {
    return "AWS_ACCESS_KEY_ID must be in AKIA/ASIA + 16 char format."
  }
  if (field === "AWS_SECRET_ACCESS_KEY" && !AWS_SECRET_RE.test(value)) {
    return "AWS_SECRET_ACCESS_KEY must be 40 valid characters."
  }
  if (field === "GOOGLE_APPLICATION_CREDENTIALS" && !value.endsWith(".json")) {
    return "Provide the path to a JSON credentials file."
  }
  return ""
}
