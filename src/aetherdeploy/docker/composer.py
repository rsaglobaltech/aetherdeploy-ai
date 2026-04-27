from __future__ import annotations

import os
import yaml

from ..models import ArchitectureProposal, ProjectAnalysis

# Imágenes base por lenguaje/framework
_BASE_IMAGES = {
    "node.js": "node:20-alpine",
    "typescript": "node:20-alpine",
    "python": "python:3.11-slim",
    "java": "maven:3.9-eclipse-temurin-21-alpine",
    "kotlin": "maven:3.9-eclipse-temurin-21-alpine",
    "go": "golang:1.22-alpine",
}


class DockerComposer:
    """Genera docker-compose.yml infiriendo servicios auxiliares del ProjectAnalysis."""

    def generate(self, analysis: ProjectAnalysis, proposal: ArchitectureProposal) -> str:
        services: dict = {}

        # Servicio principal de la aplicación
        services["app"] = self._app_service(analysis)

        # Servicios auxiliares inferidos de las dependencias
        hints = set(analysis.infrastructure_hints)
        deps = set(analysis.dependencies)

        if "has-database" in hints or self._has_db_dep(deps):
            services["db"] = self._postgres_service()
            # Inyectar variables de conexión individuales (compatibles con Spring Boot / JDBC)
            services["app"].setdefault("environment", [])
            services["app"]["environment"].extend([
                "DATABASE_URL=postgresql://app:app@db:5432/app",
                "DB_HOST=db",
                "DB_PORT=5432",
                "DB_NAME=app",
                "DB_USER=app",
                "DB_PASSWORD=app",
            ])
            services["app"].setdefault("depends_on", []).append("db")

        if "has-cache" in hints or any(d in deps for d in ("redis", "aioredis", "ioredis", "bull", "bullmq")):
            services["cache"] = self._redis_service()
            services["app"].setdefault("environment", [])
            services["app"]["environment"].append("REDIS_URL=redis://cache:6379")
            services["app"].setdefault("depends_on", []).append("cache")

        compose = {
            "version": "3.8",
            "services": services,
            "networks": {"app-net": {"driver": "bridge"}},
        }

        if "db" in services:
            compose["volumes"] = {"postgres-data": {}}

        return yaml.dump(compose, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _app_service(self, analysis: ProjectAnalysis) -> dict:
        ports = analysis.exposed_ports or [8080]
        port = ports[0]
        base_image = _BASE_IMAGES.get(analysis.primary_language.lower(), "alpine:latest")

        svc: dict = {
            "networks": ["app-net"],
            "ports": [f"{port}:{port}"],
            "restart": "unless-stopped",
        }

        if analysis.has_dockerfile:
            svc["build"] = {"context": ".", "dockerfile": "Dockerfile"}
        else:
            svc["image"] = base_image
            svc["working_dir"] = "/app"
            svc["volumes"] = [".:/app"]
            svc["command"] = self._default_command(analysis)

        return svc

    def _postgres_service(self) -> dict:
        return {
            "image": "postgres:16-alpine",
            "environment": [
                "POSTGRES_USER=app",
                "POSTGRES_PASSWORD=app",
                "POSTGRES_DB=app",
            ],
            "volumes": ["postgres-data:/var/lib/postgresql/data"],
            "networks": ["app-net"],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U app"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }

    def _redis_service(self) -> dict:
        return {
            "image": "redis:7-alpine",
            "networks": ["app-net"],
            "healthcheck": {
                "test": ["CMD", "redis-cli", "ping"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }

    def _default_command(self, analysis: ProjectAnalysis) -> str:
        lang = analysis.primary_language.lower()
        fw = (analysis.frameworks[0].lower() if analysis.frameworks else "").lower()
        if lang in ("node.js", "typescript"):
            return "npm start"
        if "fastapi" in fw or "uvicorn" in fw:
            return "uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
        if "django" in fw:
            return "python manage.py runserver 0.0.0.0:8000"
        if "flask" in fw:
            return "flask run --host=0.0.0.0"
        if lang == "python":
            return "python main.py"
        if lang == "go":
            return "go run ."
        if lang == "java" or lang == "kotlin":
            # Maven wrapper preferred, fallback to mvn
            return "sh -c 'if [ -f mvnw ]; then ./mvnw spring-boot:run -q; elif [ -f pom.xml ]; then mvn spring-boot:run -q; else java -jar target/*.jar; fi'"
        return "sh -c 'echo Start command not detected'"

    def generate_nginx_conf(self, analysis: ProjectAnalysis) -> str:
        """Genera nginx.conf que actúa como reverse proxy / load balancer hacia la app.

        Simula el ALB/Application Gateway de producción para que el entorno
        feature sea lo más parecido posible al entorno real.
        """
        port = (analysis.exposed_ports or [8080])[0]
        return (
            f"upstream app_upstream {{\n"
            f"    server app:{port};\n"
            f"}}\n\n"
            f"server {{\n"
            f"    listen 80;\n"
            f"    server_name _;\n"
            f"    access_log /var/log/nginx/access.log;\n"
            f"    error_log  /var/log/nginx/error.log;\n\n"
            f"    location / {{\n"
            f"        proxy_pass         http://app_upstream;\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header   Host              $host;\n"
            f"        proxy_set_header   X-Real-IP         $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;\n"
            f"        proxy_set_header   X-Forwarded-Proto $scheme;\n"
            f"        proxy_connect_timeout 10s;\n"
            f"        proxy_read_timeout    60s;\n"
            f"        proxy_buffer_size     16k;\n"
            f"        proxy_buffers         4 32k;\n"
            f"    }}\n\n"
            f"    location /_lb_health {{\n"
            f"        return 200 'OK';\n"
            f"        add_header Content-Type text/plain;\n"
            f"    }}\n"
            f"}}\n"
        )

    def generate_feature(self, analysis: ProjectAnalysis, proposal: ArchitectureProposal) -> str:
        """Genera docker-compose para entorno feature con LocalStack integrado.

        El compose resultante incluye todos los servicios de ``generate()`` más:
        - Un servicio ``localstack`` que emula la API de AWS en localhost:4566
        - Un servicio ``nginx`` que actúa como load balancer (simula ALB/AGIC en prod)
        - Health checks con condition-based depends_on en todos los servicios
        La app espera a que LocalStack esté saludable antes de arrancar.
        """
        data = yaml.safe_load(self.generate(analysis, proposal))

        # El compose se escribe en .aetherdeploy/ — corregir rutas relativas
        # para que apunten al raíz del proyecto (un nivel arriba)
        app_svc = data["services"]["app"]
        if "volumes" in app_svc:
            app_svc["volumes"] = [
                v.replace(".:/app", "../:/app") if v == ".:/app" else v
                for v in app_svc["volumes"]
            ]
        if "build" in app_svc:
            ctx = app_svc["build"].get("context", ".")
            if ctx == ".":
                app_svc["build"]["context"] = ".."

        # ── LocalStack ────────────────────────────────────────────────────────
        localstack_env = [
            "SERVICES=s3,ecs,iam,cloudwatch,logs,secretsmanager,rds,elasticache,ec2,elbv2",
            "DEBUG=0",
            "DOCKER_HOST=unix:///var/run/docker.sock",
            "LOCALSTACK_HOST=localhost",
        ]
        token = os.environ.get("LOCALSTACK_AUTH_TOKEN", "")
        if token:
            localstack_env.append(f"LOCALSTACK_AUTH_TOKEN={token}")

        data["services"]["localstack"] = {
            "image": "localstack/localstack:latest",
            "ports": ["4566:4566", "4510-4559:4510-4559"],
            "environment": localstack_env,
            "volumes": [
                "localstack-data:/var/lib/localstack",
                "/var/run/docker.sock:/var/run/docker.sock",
            ],
            "networks": ["app-net"],
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://localhost:4566/_localstack/health"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
            },
        }
        data.setdefault("volumes", {})["localstack-data"] = {}

        # ── Health check en la app (necesario para depends_on condition) ─────
        # start_period da margen de arranque a JVM / Django, etc.
        port = (analysis.exposed_ports or [8080])[0]
        app_svc = data["services"]["app"]
        if "healthcheck" not in app_svc:
            health_cmd = (
                f"curl -sf http://localhost:{port}/actuator/health "
                f"|| curl -sf http://localhost:{port}/health "
                f"|| curl -sf http://localhost:{port}/ || exit 1"
            )
            app_svc["healthcheck"] = {
                "test": ["CMD-SHELL", health_cmd],
                "interval": "15s",
                "timeout": "10s",
                "retries": 5,
                "start_period": "60s",
            }

        # ── depends_on: convertir listas a condition-based dicts ──────────────
        # Garantiza que ningún servicio arranca hasta que sus deps estén healthy
        raw_deps: list | dict = app_svc.get("depends_on", [])
        if isinstance(raw_deps, list):
            app_svc["depends_on"] = {
                dep: {"condition": "service_healthy"} for dep in raw_deps
            }
        # Añadir localstack como dependencia condition-based
        app_svc["depends_on"]["localstack"] = {"condition": "service_healthy"}

        # ── AWS env vars hacia LocalStack ──────────────────────────────────────
        app_svc.setdefault("environment", [])
        env_list: list = app_svc["environment"]
        if not any("AWS_ENDPOINT_URL" in str(e) for e in env_list):
            env_list.extend([
                "AWS_ENDPOINT_URL=http://localstack:4566",
                "AWS_ACCESS_KEY_ID=test",
                "AWS_SECRET_ACCESS_KEY=test",
                "AWS_DEFAULT_REGION=us-east-1",
            ])

        # ── nginx: load balancer local (simula ALB / Application Gateway) ─────
        # El nginx.feature.conf se escribe junto al compose por execution.py
        data["services"]["nginx"] = {
            "image": "nginx:1.27-alpine",
            "ports": ["80:80"],
            "volumes": ["./nginx.feature.conf:/etc/nginx/conf.d/default.conf:ro"],
            "networks": ["app-net"],
            "restart": "unless-stopped",
            "depends_on": {"app": {"condition": "service_healthy"}},
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://localhost/_lb_health"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 3,
            },
        }

        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _has_db_dep(self, deps: set[str]) -> bool:
        db_deps = {"psycopg2", "psycopg", "pg", "mysql2", "mongoose", "sequelize", "prisma",
                   "sqlalchemy", "tortoise", "django", "typeorm", "drizzle-orm"}
        return bool(db_deps.intersection(deps))
