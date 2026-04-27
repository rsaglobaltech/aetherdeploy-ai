const express = require("express")
const cors = require("cors")
const helmet = require("helmet")
const morgan = require("morgan")

const productsRouter = require("./routes/products")
const { requestId } = require("./middleware/request-id")
const { errorHandler, notFoundHandler } = require("./middleware/errors")

function createApp() {
  const app = express()

  app.disable("x-powered-by")
  app.use(helmet())
  app.use(cors())
  app.use(express.json({ limit: "1mb" }))
  app.use(requestId)
  app.use(morgan("combined", { skip: () => process.env.NODE_ENV === "test" }))

  app.get("/", (_req, res) => {
    res.json({
      name: "sample-node-project",
      status: "ok",
      docs: "/api/products",
    })
  })

  app.get("/health", (_req, res) => {
    res.json({
      status: "healthy",
      uptime: Math.round(process.uptime()),
      timestamp: new Date().toISOString(),
    })
  })

  app.use("/api/products", productsRouter)
  app.use(notFoundHandler)
  app.use(errorHandler)

  return app
}

module.exports = { createApp }
