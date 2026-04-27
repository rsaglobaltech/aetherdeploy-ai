const crypto = require("crypto")

function requestId(req, res, next) {
  const id = req.header("x-request-id") || crypto.randomUUID()
  req.id = id
  res.setHeader("x-request-id", id)
  next()
}

module.exports = { requestId }
