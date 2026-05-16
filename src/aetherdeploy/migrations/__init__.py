"""Database migration support (MEJORAS.md §2.2)."""
from .detector import (
    MigrationPlan,
    MigrationTool,
    detect_migrations,
    plans_for_project,
)
from .runner import (
    MigrationRunResult,
    run_migration_docker,
    run_migration_ecs,
)

__all__ = [
    "MigrationPlan",
    "MigrationRunResult",
    "MigrationTool",
    "detect_migrations",
    "plans_for_project",
    "run_migration_docker",
    "run_migration_ecs",
]
