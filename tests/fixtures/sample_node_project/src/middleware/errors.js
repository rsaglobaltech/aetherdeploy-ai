function notFoundHandler(req, res) {
  res.status(404).json({
    error: "not_found",
    path: req.originalUrl,
  })
}

function errorHandler(err, req, res, _next) {
  console.error({ requestId: req.id, err })
  res.status(err.status || 500).json({
    error: "internal_error",
    requestId: req.id,
  })
}

module.exports = { notFoundHandler, errorHandler }
