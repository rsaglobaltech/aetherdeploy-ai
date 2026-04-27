const express = require("express")
const { products } = require("../store/products")

const router = express.Router()

router.get("/", (_req, res) => {
  res.json({ data: products.list() })
})

router.get("/:id", (req, res) => {
  const product = products.find(req.params.id)
  if (!product) {
    return res.status(404).json({ error: "product_not_found" })
  }
  return res.json({ data: product })
})

router.post("/", (req, res) => {
  const { name, price } = req.body
  if (!name || typeof price !== "number") {
    return res.status(400).json({ error: "invalid_product" })
  }

  const product = products.create({ name, price })
  return res.status(201).json({ data: product })
})

module.exports = router
