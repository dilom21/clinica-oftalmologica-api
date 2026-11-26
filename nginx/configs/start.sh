#!/bin/sh
set -eu

case "${NGINX_MODE:-http}" in
  http|https) ;;
  *) echo 'NGINX_MODE must be http or https' >&2; exit 1 ;;
esac

if [ -z "${DUCKDNS_FQDN:-}" ]; then
  echo 'DUCKDNS_FQDN must be configured' >&2
  exit 1
fi

# Restrict substitution to the one deployment variable: preserve Nginx $host,
# $scheme, $request_uri and other built-in runtime variables verbatim.
envsubst '${DUCKDNS_FQDN}' \
  < "/etc/nginx/configs/${NGINX_MODE}.conf.template" \
  > /etc/nginx/conf.d/default.conf

nginx -t
exec nginx -g 'daemon off;'
