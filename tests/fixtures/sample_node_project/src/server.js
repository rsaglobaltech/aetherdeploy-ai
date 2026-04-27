const { createApp } = require("./app")

const port = Number(process.env.PORT || 3000)
const host = process.env.HOST || "0.0.0.0"

const app = createApp()

const server = app.listen(port, host, () => {
  console.log(`sample-node-project listening on http://${host}:${port}`)
})

function shutdown(signal) {
  console.log(`${signal} received, closing HTTP server`)
  server.close(() => process.exit(0))
}

process.on("SIGTERM", () => shutdown("SIGTERM"))
process.on("SIGINT", () => shutdown("SIGINT"))
