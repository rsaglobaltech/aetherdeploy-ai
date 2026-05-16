"""Migration tool detection (MEJORAS.md §2.2).

Walks a project tree looking for the conventional layout of each supported
migration tool and emits a :class:`MigrationPlan` describing what to run, what
image to use, and which environment variable carries the database URL. The
plan is provider-agnostic — runners decide how to execute it (a local docker
container, an ECS one-off task, a Cloud Run Job, etc.).

Detection precedence is deterministic: a project with both Flyway resources
and an Alembic config returns one plan per tool. The order returned by
:func:`plans_for_project` is the same order in which they should run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


MigrationTool = Literal[
    "flyway",
    "alembic",
    "prisma",
    "liquibase",
    "django",
    "knex",
    "rails",
    "goose",
]


@dataclass
class MigrationPlan:
    tool: MigrationTool
    migrations_dir: Path
    command: list[str]                     # argv inside the migration container
    image_hint: str                        # default OCI image when no app image suits
    env_var: str                           # name of the env var that carries the DB URL
    description: str
    detected_at: Path                      # the marker file/dir that triggered the match
    extras: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "migrations_dir": str(self.migrations_dir),
            "command": list(self.command),
            "image_hint": self.image_hint,
            "env_var": self.env_var,
            "description": self.description,
            "detected_at": str(self.detected_at),
            "extras": dict(self.extras),
        }


# ---------------------------------------------------------------------------
# Per-tool detectors
# ---------------------------------------------------------------------------

def _detect_flyway(project_path: Path) -> MigrationPlan | None:
    """Spring Boot puts SQL under ``src/main/resources/db/migration/``; standalone
    Flyway typically lives in ``db/migration/`` or ``flyway/``."""
    candidates = [
        project_path / "src" / "main" / "resources" / "db" / "migration",
        project_path / "db" / "migration",
        project_path / "flyway" / "sql",
    ]
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        sql_files = [p for p in candidate.glob("V*.sql")]
        if not sql_files:
            continue
        return MigrationPlan(
            tool="flyway",
            migrations_dir=candidate,
            command=["flyway", "-locations=filesystem:/flyway/sql", "migrate"],
            image_hint="flyway/flyway:10-alpine",
            env_var="FLYWAY_URL",
            description=f"Flyway: {len(sql_files)} versioned scripts under {candidate.relative_to(project_path)}",
            detected_at=candidate,
            extras={"script_count": str(len(sql_files))},
        )
    return None


def _detect_alembic(project_path: Path) -> MigrationPlan | None:
    """Alembic is identified by ``alembic.ini`` at the project root or in ``alembic/``."""
    ini = project_path / "alembic.ini"
    if not ini.is_file():
        ini = project_path / "alembic" / "alembic.ini"
        if not ini.is_file():
            return None
    versions_dir = ini.parent / "alembic" / "versions"
    if not versions_dir.is_dir():
        versions_dir = ini.parent / "versions"
    return MigrationPlan(
        tool="alembic",
        migrations_dir=versions_dir if versions_dir.is_dir() else ini.parent,
        command=["alembic", "upgrade", "head"],
        image_hint="python:3.12-slim",
        env_var="DATABASE_URL",
        description="Alembic: `alembic upgrade head`",
        detected_at=ini,
    )


def _detect_prisma(project_path: Path) -> MigrationPlan | None:
    """Prisma stores its schema in ``prisma/schema.prisma``; migrations sit under
    ``prisma/migrations/``."""
    schema = project_path / "prisma" / "schema.prisma"
    if not schema.is_file():
        return None
    migrations_dir = project_path / "prisma" / "migrations"
    return MigrationPlan(
        tool="prisma",
        migrations_dir=migrations_dir if migrations_dir.is_dir() else schema.parent,
        command=["npx", "prisma", "migrate", "deploy"],
        image_hint="node:20-alpine",
        env_var="DATABASE_URL",
        description="Prisma: `prisma migrate deploy`",
        detected_at=schema,
    )


def _detect_liquibase(project_path: Path) -> MigrationPlan | None:
    candidates = [
        project_path / "liquibase.properties",
        project_path / "src" / "main" / "resources" / "db" / "changelog",
        project_path / "db" / "changelog",
    ]
    for candidate in candidates:
        if candidate.exists():
            changelog_dir = candidate if candidate.is_dir() else candidate.parent
            return MigrationPlan(
                tool="liquibase",
                migrations_dir=changelog_dir,
                command=["liquibase", "update"],
                image_hint="liquibase/liquibase:4-alpine",
                env_var="LIQUIBASE_COMMAND_URL",
                description="Liquibase: `liquibase update`",
                detected_at=candidate,
            )
    return None


def _detect_django(project_path: Path) -> MigrationPlan | None:
    manage = project_path / "manage.py"
    if not manage.is_file():
        return None
    # Django's "do you have migrations?" answer is "you almost always do" — but
    # we still need the project to actually use the migrations framework.
    has_migrations = any(project_path.glob("**/migrations/*.py"))
    if not has_migrations:
        return None
    return MigrationPlan(
        tool="django",
        migrations_dir=project_path,
        command=["python", "manage.py", "migrate", "--noinput"],
        image_hint="python:3.12-slim",
        env_var="DATABASE_URL",
        description="Django: `manage.py migrate --noinput`",
        detected_at=manage,
    )


def _detect_knex(project_path: Path) -> MigrationPlan | None:
    config = project_path / "knexfile.js"
    if not config.is_file():
        config = project_path / "knexfile.ts"
        if not config.is_file():
            return None
    return MigrationPlan(
        tool="knex",
        migrations_dir=project_path,
        command=["npx", "knex", "migrate:latest"],
        image_hint="node:20-alpine",
        env_var="DATABASE_URL",
        description="Knex: `knex migrate:latest`",
        detected_at=config,
    )


def _detect_rails(project_path: Path) -> MigrationPlan | None:
    schema_rb = project_path / "db" / "schema.rb"
    migrate_dir = project_path / "db" / "migrate"
    if not (schema_rb.is_file() or migrate_dir.is_dir()):
        return None
    return MigrationPlan(
        tool="rails",
        migrations_dir=migrate_dir if migrate_dir.is_dir() else schema_rb.parent,
        command=["bundle", "exec", "rails", "db:migrate"],
        image_hint="ruby:3.3-slim",
        env_var="DATABASE_URL",
        description="Rails: `rails db:migrate`",
        detected_at=migrate_dir if migrate_dir.is_dir() else schema_rb,
    )


def _detect_goose(project_path: Path) -> MigrationPlan | None:
    candidates = [project_path / "migrations", project_path / "db" / "migrations"]
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        sql_files = list(candidate.glob("*.sql"))
        # Heuristic: goose tags scripts as ``0001_init.sql`` plus ``-- +goose Up``.
        if any("+goose" in (p.read_text(errors="ignore")[:2048]) for p in sql_files):
            return MigrationPlan(
                tool="goose",
                migrations_dir=candidate,
                command=["goose", "-dir", str(candidate), "postgres", "$DATABASE_URL", "up"],
                image_hint="ghcr.io/pressly/goose:latest",
                env_var="DATABASE_URL",
                description=f"Goose: {len(sql_files)} scripts under {candidate.relative_to(project_path)}",
                detected_at=candidate,
            )
    return None


_DETECTORS = (
    _detect_flyway,
    _detect_liquibase,
    _detect_alembic,
    _detect_django,
    _detect_prisma,
    _detect_knex,
    _detect_rails,
    _detect_goose,
)


def detect_migrations(project_path: Path) -> list[MigrationPlan]:
    """Returns one :class:`MigrationPlan` per detected tool.

    Multi-tool projects are rare in the wild, but the function does not stop
    at the first match — surface every tool so the operator decides which to
    keep.
    """
    plans: list[MigrationPlan] = []
    for detect in _DETECTORS:
        plan = detect(project_path)
        if plan is not None:
            plans.append(plan)
    return plans


def plans_for_project(project_path: Path) -> list[MigrationPlan]:
    """Public alias kept stable for SDK consumers."""
    return detect_migrations(project_path)


def select_primary_plan(plans: Iterable[MigrationPlan]) -> MigrationPlan | None:
    """When multiple tools are detected, pick the one most likely to be the source of truth.

    Rule of thumb: ORMs that own the schema (Django, Rails, Prisma) win over
    raw SQL runners (Flyway, Liquibase, Goose) because running an ORM migration
    typically applies what the raw runner would. Alembic is between the two.
    """
    priority = {
        "django": 0,
        "rails": 1,
        "prisma": 2,
        "alembic": 3,
        "knex": 4,
        "flyway": 5,
        "liquibase": 6,
        "goose": 7,
    }
    return min(plans, key=lambda p: priority.get(p.tool, 99), default=None)
