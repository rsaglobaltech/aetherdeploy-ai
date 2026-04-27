# Sample Node Project

Complete Node.js + Express fixture for AetherDeploy.

## Run

```bash
npm install
npm start
```

The API listens on port `3000`.

## Endpoints

- `GET /health`
- `GET /api/products`
- `GET /api/products/:id`
- `POST /api/products`

## LocalStack

```bash
docker compose -f docker-compose.localstack.yml up -d
```
