import { render } from "ink"
import React from "react"
import { App } from "./App.js"

const args = process.argv.slice(2)

const projectIdx = args.findIndex(a => a === "--project" || a === "-p")
const projectPath = projectIdx >= 0 ? args[projectIdx + 1] : undefined

const envIdx = args.findIndex(a => a === "--env" || a === "-e")
const envs = envIdx >= 0 ? [args[envIdx + 1]] : undefined

const dryRun = args.includes("--dry-run")

render(<App projectPath={projectPath} envs={envs} dryRun={dryRun} />)
