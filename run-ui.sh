#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Run ./setup.sh first." >&2
  exit 1
fi

cd "$REPO_ROOT"
exec "$PYTHON" -m video_workflow.ui "$@"
