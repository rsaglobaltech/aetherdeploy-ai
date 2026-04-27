const crypto = require("crypto")

const state = [
  { id: "prod_espresso", name: "Espresso", price: 3.5 },
  { id: "prod_filter", name: "Filter coffee", price: 4.25 },
]

const products = {
  list() {
    return [...state]
  },

  find(id) {
    return state.find((product) => product.id === id)
  },

  create({ name, price }) {
    const product = {
      id: `prod_${crypto.randomUUID().slice(0, 8)}`,
      name,
      price,
    }
    state.push(product)
    return product
  },
}

module.exports = { products }
