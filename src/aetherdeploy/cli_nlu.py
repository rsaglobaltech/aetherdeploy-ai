"""Natural-language understanding helpers for the AetherDeploy CLI.

Provides intent detection (action / provider / environment) from free-form
text in English or Spanish, used by both the classic CLI and the stream mode.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns — ordered most-specific to least-specific; first match wins
# ---------------------------------------------------------------------------

_DESTROY_RE = re.compile(
    r"""(?ix)
    \b(
        destroy | tear[\s\-]?down | delete[\s\-]?all | remove[\s\-]+(all[\s\-])?resources |
        cleanup | clean[\s\-]up | wipe[\s\-]+(out|infrastructure) |
        # Spanish
        destruye? | elimina | borra | limpiar | derribar |
        eliminar[\s\-]?todo | borrar[\s\-]?(todo|recursos|infraestructura) |
        destruir[\s\-]?(todo|recursos|infraestructura)
    )\b
""",
)

_PLAN_RE = re.compile(
    r"""(?ix)
    \b(
        plan | preview | previsualiz | show[\s\-]?changes? | dry[\s\-]?run |
        what[\s\-]would | estimate |
        # Spanish
        planifica? | planificar | planificaci[oó]n | muestra[\s\-]cambios |
        previsuali[sz] | sin[\s\-]aplicar | calcular[\s\-]?plan
    )\b
""",
)

_INIT_RE = re.compile(
    r"""(?ix)
    \b(
        init | initialize | analyse | analyze | inspect | scan[\s\-]?project |
        detect[\s\-]?stack |
        # Spanish
        inicializ | analiz | escanear | detectar[\s\-]stack
    )\b
""",
)

_EXPLICIT_DEPLOY_RE = re.compile(
    r"""(?ix)
    \b(
        deploy | launch | release | ship | push[\s\-]?to | go[\s\-]?live |
        # Spanish
        desplieg[ao] | despliega? | desplegar | lanzar | publicar
    )\b
""",
)

_PROVIDER_RE = re.compile(
    r"""(?ix)
    \b(aws|amazon|gcp|google(?:[\s\-]cloud)?|azure|microsoft(?:[\s\-]azure)?)
    \b
""",
)

_PROVIDER_MAP: dict[str, str] = {
    "aws": "aws", "amazon": "aws",
    "gcp": "gcp", "google": "gcp", "googlecloud": "gcp",
    "azure": "azure", "microsoft": "azure", "microsoftazure": "azure",
}

_ENV_RE = re.compile(
    r"""(?ix)
    \b(
        prod(?:uction)? | staging | feature | local | dev(?:elopment)?
    )\b
""",
)

_ENV_MAP: dict[str, str] = {
    "prod": "prod", "production": "prod",
    "staging": "staging",
    "feature": "feature",
    "local": "local",
    "dev": "feature", "development": "feature",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_intent(text: str) -> tuple[str, str | None, list[str]]:
    """Detect (action, provider, environments) from free-form text.

    Always returns an action — defaults to ``"deploy"`` when no keyword matches.
    Use :func:`detect_explicit_action` when you only want to act on clear signals.

    Returns:
        action    — "init" | "plan" | "deploy" | "destroy"
        provider  — "aws" | "gcp" | "azure" | None
        envs      — list of canonical environment names, possibly empty
    """
    if _DESTROY_RE.search(text):
        action = "destroy"
    elif _PLAN_RE.search(text):
        action = "plan"
    elif _INIT_RE.search(text):
        action = "init"
    else:
        action = "deploy"

    provider_match = _PROVIDER_RE.search(text)
    provider: str | None = None
    if provider_match:
        key = re.sub(r"[\s\-]", "", provider_match.group(1).lower())
        provider = _PROVIDER_MAP.get(key)

    envs: list[str] = []
    for m in _ENV_RE.finditer(text):
        key = m.group(1).lower()
        env = _ENV_MAP.get(key)
        if env and env not in envs:
            envs.append(env)

    return action, provider, envs


def detect_explicit_action(text: str) -> str | None:
    """Returns the action only when an explicit keyword is present, else None.

    Unlike :func:`detect_intent`, this never defaults to ``"deploy"``.
    Used for follow-up messages after initialization to avoid treating casual
    questions as action commands.
    """
    if _DESTROY_RE.search(text):
        return "destroy"
    if _PLAN_RE.search(text):
        return "plan"
    if _INIT_RE.search(text):
        return "init"
    if _EXPLICIT_DEPLOY_RE.search(text):
        return "deploy"
    return None


def parse_requested_action(content: str) -> tuple[str, str]:
    """Map a user message (slash command or natural language) to (action, instruction)."""
    stripped = content.strip()
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower() if stripped.startswith("/") else ""
    rest = parts[1] if len(parts) > 1 else ""

    if command == "/init":
        return "init", rest or "initialize this project"
    if command == "/plan":
        return "plan", rest or "plan the deployment of this project"
    if command == "/deploy":
        return "deploy", rest or "deploy this project"
    if command == "/destroy":
        return "destroy", rest or "destroy all resources for this project"

    action, _provider, _envs = detect_intent(stripped)
    return action, stripped


def parse_env_reply(text: str) -> str | None:
    """Parse an environment name from free-form text. Returns canonical name or None."""
    t = text.strip().lower()
    for token in re.split(r"[\s,/|]+", t):
        if token in _ENV_MAP:
            return _ENV_MAP[token]
    if any(w in t for w in ("prod", "production", "real", "live")):
        return "prod"
    if any(w in t for w in ("feature", "local", "dev", "development", "sandbox", "test", "localstack")):
        return "feature"
    if any(w in t for w in ("staging", "stage", "pre-prod", "preprod", "qa")):
        return "staging"
    return None


def extract_path_hint(text: str) -> str | None:
    """Try to extract a valid directory path from a natural-language message."""
    from pathlib import Path

    patterns = [
        r'["\']([^"\']+)["\']',
        r'(?:^|\s)(~[/\w\-\.]+)',
        r'(?:^|\s)(/[\w/\-\.]+)',
        r'(?:at|in|from|of)\s+([\w\.~][/\w\-\.]+)',
        r'(\.\.?/[\w\-\.]+(?:/[\w\-\.]+)*)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            try:
                resolved = Path(candidate).expanduser().resolve()
                if resolved.is_dir():
                    return str(resolved)
            except Exception:
                pass
    return None


def resolve_project_path(candidate: str) -> str | None:
    """Resolve and validate a path string. Returns absolute path string, or None."""
    from pathlib import Path

    try:
        resolved = Path(candidate.strip().strip("\"'")).expanduser().resolve()
        if resolved.is_dir():
            return str(resolved)
    except Exception:
        pass
    return None
