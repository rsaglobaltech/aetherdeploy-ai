#!/usr/bin/env node
// Entry point del CLI de AetherDeploy — usa tsx para ejecutar TypeScript directamente
import { createRequire } from "module"
import { fileURLToPath } from "url"
import { dirname, join } from "path"

const __dirname = dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

// tsx permite ejecutar TypeScript sin compilar explícitamente
const { register } = await import("tsx/esm")
await import(join(__dirname, "../src/index.tsx"))
