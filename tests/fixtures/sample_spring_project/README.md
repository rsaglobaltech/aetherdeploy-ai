# Demo Spring Boot

REST API con Spring Boot 3 + JPA + PostgreSQL.

## Endpoints

| Método | Ruta      | Descripción      |
|--------|-----------|------------------|
| GET    | `/`       | Estado general   |
| GET    | `/health` | Health check     |

## Requisitos

- Java 21
- Maven 3.9+
- PostgreSQL 16

## Desarrollo local

```bash
./mvnw spring-boot:run
```

Con Docker:

```bash
docker compose up
```
