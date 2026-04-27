const test = require("node:test")
const assert = require("node:assert/strict")
const request = require("supertest")

const { createApp } = require("../src/app")

test("GET /health returns healthy status", async () => {
  const response = await request(createApp()).get("/health")

  assert.equal(response.status, 200)
  assert.equal(response.body.status, "healthy")
})

test("GET /api/products lists products", async () => {
  const response = await request(createApp()).get("/api/products")

  assert.equal(response.status, 200)
  assert.ok(Array.isArray(response.body.data))
  assert.ok(response.body.data.length >= 2)
})

test("POST /api/products validates payload", async () => {
  const response = await request(createApp()).post("/api/products").send({ name: "Latte" })

  assert.equal(response.status, 400)
  assert.equal(response.body.error, "invalid_product")
})
