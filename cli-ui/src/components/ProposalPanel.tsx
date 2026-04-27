import { Box, Text } from "ink"
import React from "react"
import type { ArchitectureProposal } from "../types.js"

interface Props {
  proposal: ArchitectureProposal
}

export function ProposalPanel({ proposal }: Props) {
  const primaryServices = proposal.services.slice(0, 4)
  const environmentText = (proposal.environments ?? [])
    .map(env => `${env.name}:${labelStrategy(env.strategy)}`)
    .join("  ")
  const securityNotes = (proposal.securityNotes ?? []).slice(0, 2)
  const scalabilityNotes = (proposal.scalabilityNotes ?? []).slice(0, 2)

  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderColor="cyan"
      paddingX={2}
      paddingY={1}
      marginX={1}
    >
      <Box justifyContent="space-between">
        <Box gap={1}>
          <Text bold color="white">Architecture Summary</Text>
          <Text color="cyan">{proposal.provider.toUpperCase()}</Text>
          <Text color="gray">{proposal.region}</Text>
        </Box>
        <Text bold color="green">{formatCost(proposal.totalCost)}</Text>
      </Box>

      {environmentText && (
        <Box marginTop={1}>
          <Text color="gray">Environments: </Text>
          <Text color="white">{environmentText}</Text>
        </Box>
      )}

      <Box marginTop={1}>
        <Text color="gray">Goal: </Text>
        <Text color="white">{buildObjective(proposal)}</Text>
      </Box>

      <Box flexDirection="column" marginTop={1}>
        <Text bold color="white">Proposed services</Text>
        {primaryServices.map((svc, i) => (
          <Box key={i} flexDirection="column" marginTop={i === 0 ? 0 : 1}>
            <Box gap={1}>
              <Text color="cyan">•</Text>
              <Text bold color="white">{svc.service}</Text>
              <Text color="gray">[{svc.purpose}]</Text>
              <Text color="green">{formatCost(svc.cost)}</Text>
            </Box>
            <Box paddingLeft={2}>
              <Text color="gray">{svc.justification}</Text>
            </Box>
          </Box>
        ))}
      </Box>

      {(securityNotes.length > 0 || scalabilityNotes.length > 0) && (
        <Box flexDirection="column" marginTop={1}>
          {securityNotes.length > 0 && (
            <Box flexDirection="column">
              <Text bold color="white">Security</Text>
              {securityNotes.map((note, i) => (
                <Box key={i} paddingLeft={2}>
                  <Text color="gray">- {note}</Text>
                </Box>
              ))}
            </Box>
          )}

          {scalabilityNotes.length > 0 && (
            <Box flexDirection="column" marginTop={securityNotes.length > 0 ? 1 : 0}>
              <Text bold color="white">Scalability</Text>
              {scalabilityNotes.map((note, i) => (
                <Box key={i} paddingLeft={2}>
                  <Text color="gray">- {note}</Text>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      )}

      <Box marginTop={1}>
        <Text color="gray">Next step: </Text>
        <Text color="white">/plan calculates Terraform changes; /deploy applies the selected environment.</Text>
      </Box>
    </Box>
  )
}

function formatCost(value: string) {
  const trimmed = value.trim()
  return /\/mo|month/i.test(trimmed) ? trimmed : `${trimmed}/mo`
}

function labelStrategy(strategy: string) {
  if (strategy === "ephemeral") return "feature"
  if (strategy === "terraform") return "Terraform"
  if (strategy === "docker") return "Docker"
  return strategy
}

function buildObjective(proposal: ArchitectureProposal) {
  const purposes = proposal.services.map(svc => svc.purpose)
  const hasCompute = purposes.includes("compute")
  const hasNetwork = purposes.includes("networking")
  const hasDatabase = purposes.includes("database")
  const parts = []

  if (hasCompute) parts.push("run the application in managed containers")
  if (hasNetwork) parts.push("expose it with networking and load balancing")
  if (hasDatabase) parts.push("persist data in a managed service")

  return parts.length > 0
    ? `${capitalize(parts.join(", "))}.`
    : "Prepare cloud infrastructure aligned with the detected stack."
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
