#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage: pnpm sdrpp-server [-- SDR++ options]

Start an installed SDR++ in server mode (default: 127.0.0.1:5259).

Environment:
  SDRPP_BIN   SDR++ executable name or path (default: sdrpp)
  SDRPP_ADDR  Listen address (default: 127.0.0.1)
  SDRPP_PORT  Listen port (default: 5259)

Examples:
  pnpm sdrpp-server
  SDRPP_ADDR=0.0.0.0 SDRPP_PORT=5259 pnpm sdrpp-server
  pnpm sdrpp-server -- --root /path/to/sdrpp-config

Additional options are passed directly to SDR++.
HELP
  exit 0
fi

binary="${SDRPP_BIN:-sdrpp}"
if ! command -v "$binary" >/dev/null 2>&1; then
  printf 'SDR++ executable not found: %s\nInstall SDR++ from https://www.sdrpp.org/ or set SDRPP_BIN to its executable path.\n' "$binary" >&2
  exit 127
fi

exec "$binary" --server --addr "${SDRPP_ADDR:-127.0.0.1}" --port "${SDRPP_PORT:-5259}" "$@"
