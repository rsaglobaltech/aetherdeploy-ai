#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CLI_UI_BIN="${REPO_ROOT}/cli-ui/bin/aetherdeploy.js"

if [[ ! -f "${CLI_UI_BIN}" ]]; then
  echo "No se encontro el CLI UI en: ${CLI_UI_BIN}" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js no esta instalado o no esta en PATH." >&2
  exit 1
fi

ARGS=()
while (($#)); do
  case "$1" in
    --proyect)
      ARGS+=("--project")
      ;;
    *)
      ARGS+=("$1")
      ;;
  esac
  shift
done

exec node "${CLI_UI_BIN}" "${ARGS[@]}"
