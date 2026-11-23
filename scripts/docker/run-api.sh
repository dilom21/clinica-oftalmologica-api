#!/bin/sh
# Single Uvicorn launcher shared by the image CMD and Docker Compose.
# Forwarded headers are enabled only for an explicitly trusted proxy address.
set -eu

host="${UVICORN_HOST-0.0.0.0}"
port="${UVICORN_PORT-8000}"
# B-1: `${VAR-default}` (not `:-default`) distinguishes an UNSET variable from a
# variable set to an empty string, so an explicit empty value is rejected
# instead of being silently replaced by the loopback default.
forwarded="${FORWARDED_ALLOW_IPS-127.0.0.1}"

case "$forwarded" in
  *"*"*)
    echo "FORWARDED_ALLOW_IPS must be an explicit proxy IP or CIDR; wildcard is forbidden" >&2
    exit 1
    ;;
  *[![:space:]]*) ;;
  *)
    echo "FORWARDED_ALLOW_IPS is set but empty; unset it or provide the proxy IP/CIDR" >&2
    exit 1
    ;;
esac

exec python -m uvicorn app.main:app \
  --host "$host" \
  --port "$port" \
  --proxy-headers \
  --forwarded-allow-ips "$forwarded"
