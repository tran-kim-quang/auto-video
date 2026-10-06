#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
CHECK_ONLY=0
CHECK_FAILURES=0
OS_ID=""
OS_VERSION=""

if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
  shift
fi
if (( $# != 0 )); then
  echo "Usage: ./setup.sh [--check-only]" >&2
  exit 2
fi

report_check() {
  local name="$1"
  local status="$2"
  local detail="$3"
  printf '[%s] %s: %s\n' "$status" "$name" "$detail"
  if [[ "$status" == "FAIL" ]]; then
    CHECK_FAILURES=1
  fi
}

version_is_supported() {
  [[ "$1" == "24.04" || "$1" == "26.04" ]]
}

load_os_release() {
  if [[ ! -r /etc/os-release ]]; then
    return 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_ID="${ID:-}"
  OS_VERSION="${VERSION_ID:-}"
}

find_office() {
  if command -v libreoffice >/dev/null 2>&1; then
    command -v libreoffice
  elif command -v soffice >/dev/null 2>&1; then
    command -v soffice
  else
    return 1
  fi
}

check_dependencies() {
  CHECK_FAILURES=0

  if load_os_release && [[ "$OS_ID" == "ubuntu" ]] && version_is_supported "$OS_VERSION"; then
    report_check "Ubuntu" "PASS" "$OS_VERSION"
  else
    report_check "Ubuntu" "FAIL" "requires Ubuntu 24.04 or 26.04"
  fi

  if command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    report_check "Python" "PASS" "$(python3 --version 2>&1)"
  else
    report_check "Python" "FAIL" "requires Python 3.12 or newer"
  fi

  if command -v python3 >/dev/null 2>&1 && python3 -c 'import tkinter' >/dev/null 2>&1; then
    report_check "Tkinter" "PASS" "python3-tk is importable"
  else
    report_check "Tkinter" "FAIL" "install python3-tk"
  fi

  if command -v ffmpeg >/dev/null 2>&1; then
    report_check "FFmpeg" "PASS" "$(command -v ffmpeg)"
  else
    report_check "FFmpeg" "FAIL" "install ffmpeg"
  fi

  if command -v ffprobe >/dev/null 2>&1; then
    report_check "ffprobe" "PASS" "$(command -v ffprobe)"
  else
    report_check "ffprobe" "FAIL" "install ffmpeg"
  fi

  local office=""
  if office="$(find_office)"; then
    report_check "LibreOffice" "PASS" "$office"
  else
    report_check "LibreOffice" "FAIL" "install libreoffice-impress-nogui"
  fi

  if command -v pdftocairo >/dev/null 2>&1; then
    report_check "pdftocairo" "PASS" "$(command -v pdftocairo)"
  else
    report_check "pdftocairo" "FAIL" "install poppler-utils"
  fi

  if [[ -x "$VENV_PYTHON" ]] &&
    "$VENV_PYTHON" -c 'import video_workflow' >/dev/null 2>&1; then
    report_check "Virtual environment" "PASS" "$VENV_PYTHON imports video_workflow"
  else
    report_check "Virtual environment" "FAIL" "run ./setup.sh to install the project"
  fi

  return "$CHECK_FAILURES"
}

install_dependencies() {
  if ! load_os_release || [[ "$OS_ID" != "ubuntu" ]] || ! version_is_supported "$OS_VERSION"; then
    echo "This installer supports Ubuntu 24.04 and 26.04 only." >&2
    exit 1
  fi

  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-tk ffmpeg \
    libreoffice-impress-nogui poppler-utils fonts-liberation fonts-noto-core

  if [[ ! -x "$VENV_PYTHON" ]]; then
    python3 -m venv "$REPO_ROOT/.venv"
  fi
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -e "$REPO_ROOT"
}

main() {
  if (( CHECK_ONLY )); then
    check_dependencies
    return
  fi
  install_dependencies
  check_dependencies
}

main
