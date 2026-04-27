import { Box, Text } from "ink"
import React from "react"
import type { DeploymentResultData } from "../hooks/useAgent.js"

interface Props {
  result: DeploymentResultData
}

const PROVIDER_LABEL: Record<string, string> = {
  aws: "AWS",
  gcp: "GCP",
  azure: "Azure",
  localstack: "LocalStack",
}

export function DeploymentPanel({ result }: Props) {
  const providerLabel = result.provider ? (PROVIDER_LABEL[result.provider] ?? result.provider.toUpperCase()) : ""
  const envLabel = result.environments.join(", ")
  const isPlan = result.action === "plan"

  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderColor={isPlan ? "cyan" : "green"}
      paddingX={2}
      paddingY={1}
      marginX={1}
    >
      <Box justifyContent="space-between">
        <Box gap={1}>
          <Text bold color={isPlan ? "cyan" : "green"}>
            {isPlan ? "✓ Plan ready" : "✓ Deployment complete"}
          </Text>
          {providerLabel && <Text color="cyan">{providerLabel}</Text>}
          {envLabel && <Text color="gray">{envLabel}</Text>}
        </Box>
      </Box>

      {result.endpoints.length > 0 && !result.endpoints[0].startsWith("(plan") && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold color="white">Endpoints</Text>
          {result.endpoints.map((ep, i) => (
            <Box key={i} gap={1} paddingLeft={1}>
              <Text color="cyan">→</Text>
              <Text color="white">{ep}</Text>
            </Box>
          ))}
        </Box>
      )}

      {isPlan && (
        <Box marginTop={1}>
          <Text color="gray">Next step: </Text>
          <Text color="white">run /deploy to apply this configuration to the selected environment.</Text>
        </Box>
      )}
    </Box>
  )
}
