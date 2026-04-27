import chalk from "chalk"

export const theme = {
  primary:  chalk.hex("#7C3AED"),   // violeta AetherDeploy
  accent:   chalk.hex("#06B6D4"),   // cyan neón
  success:  chalk.hex("#10B981"),   // verde
  warning:  chalk.hex("#F59E0B"),   // ámbar
  error:    chalk.hex("#EF4444"),   // rojo
  muted:    chalk.hex("#6B7280"),   // gris
  text:     chalk.hex("#F9FAFB"),   // blanco suave
  dim:      chalk.dim,

  prefix: {
    user:  chalk.hex("#06B6D4")("▸"),
    agent: chalk.hex("#7C3AED")("◈"),
    error: chalk.hex("#EF4444")("✗"),
    info:  chalk.hex("#6B7280")("·"),
  },
}
