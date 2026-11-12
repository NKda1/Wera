#!/usr/bin/env bash
# Runs the OpenTelemetry Collector that sends Postgres server metrics to Logfire (OBS-04).
#
#   scripts/otel-collector.sh             run in the foreground (Ctrl-C stops it)
#   scripts/otel-collector.sh validate    check the config and the variables, send nothing
#   scripts/otel-collector.sh install     keep it running: a launchd agent, started at login and
#                                         restarted if it stops; logs in ~/Library/Logs/
#   scripts/otel-collector.sh uninstall   stop it and remove the agent
#
# Reads POSTGRESQL_USERNAME and POSTGRESQL_PASSWORD (the `logfire_monitor` role) from .env.local,
# and the project write token from .logfire/logfire_credentials.json unless LOGFIRE_TOKEN is set.
# The collector binary is `otelcol-contrib` on PATH, or OTELCOL_BIN.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="$root/infra/otel/postgresql.yaml"
label="com.wera.otel-collector"
plist="$HOME/Library/LaunchAgents/$label.plist"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "Collector stopped and its launchd agent removed."
  exit 0
fi

# Only the variables this needs, read line by line: sourcing .env.local would run it as shell.
read_env() {
  [ -f "$root/.env.local" ] || return 0
  grep -E "^$1=" "$root/.env.local" | tail -n 1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

export POSTGRESQL_USERNAME="${POSTGRESQL_USERNAME:-$(read_env POSTGRESQL_USERNAME)}"
export POSTGRESQL_PASSWORD="${POSTGRESQL_PASSWORD:-$(read_env POSTGRESQL_PASSWORD)}"
export POSTGRESQL_ENDPOINT="${POSTGRESQL_ENDPOINT:-$(read_env POSTGRESQL_ENDPOINT)}"
export POSTGRESQL_ENDPOINT="${POSTGRESQL_ENDPOINT:-localhost:5432}"
export POSTGRESQL_INSTANCE_ID="${POSTGRESQL_INSTANCE_ID:-$(hostname -s)-pg17}"
export LOGFIRE_INGEST_URL="${LOGFIRE_INGEST_URL:-$(read_env LOGFIRE_INGEST_URL)}"
export LOGFIRE_INGEST_URL="${LOGFIRE_INGEST_URL:-https://logfire-eu.pydantic.dev}"
if [ -z "${LOGFIRE_TOKEN:-}" ] && [ -f "$root/.logfire/logfire_credentials.json" ]; then
  LOGFIRE_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$root/.logfire/logfire_credentials.json")"
fi
export LOGFIRE_TOKEN="${LOGFIRE_TOKEN:-}"

for name in POSTGRESQL_USERNAME POSTGRESQL_PASSWORD LOGFIRE_TOKEN; do
  if [ -z "${!name}" ]; then
    echo "$name is not set (see .env.example, 'Postgres metrics')." >&2
    exit 1
  fi
done

bin="${OTELCOL_BIN:-$(command -v otelcol-contrib || true)}"
if [ -z "$bin" ]; then
  echo "otelcol-contrib isn't on PATH. See ARCHITECTURE §5.1 for the pinned release to install." >&2
  exit 1
fi

if [ "${1:-}" = "validate" ]; then
  exec "$bin" validate --config "$config"
fi

if [ "${1:-}" = "install" ]; then
  "$bin" validate --config "$config"
  # The agent runs this script, not the collector, so the password and token are read from
  # .env.local and .logfire/ at each start and never written into the plist.
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$root/scripts/otel-collector.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OTELCOL_BIN</key><string>$bin</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/wera-otel-collector.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/wera-otel-collector.log</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "Collector running under launchd ($label); logs: ~/Library/Logs/wera-otel-collector.log"
  exit 0
fi
exec "$bin" --config "$config"
