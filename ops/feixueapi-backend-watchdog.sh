#!/usr/bin/env bash
set -u

STATE_DIR=/run/feixueapi-backend-watchdog
REGISTER_DB=/opt/turb-gpt-register/turb.sqlite3

log() {
  /usr/bin/logger -t feixueapi-backend-watchdog -- "$*" 2>/dev/null || true
}

mkdir -p "$STATE_DIR"

probe() {
  local port=$1 host=$2
  /usr/bin/curl --silent --show-error --fail --noproxy '*' \
    --connect-timeout 3 --max-time 8 -H "Host: $host" \
    "http://127.0.0.1:$port/" -o /dev/null
}

# A slow WebUI probe must not kill an active registration batch. Return 0 when
# the queue is active, 1 when it is empty, and 2 when its state is unknown.
register_queue_state() {
  local count
  if [[ ! -r "$REGISTER_DB" ]]; then
    return 2
  fi
  count=$(/usr/bin/sqlite3 -readonly "$REGISTER_DB" \
    'SELECT COUNT(*) FROM registration_jobs WHERE status IN ("pending","running","stopping");' \
    2>/dev/null) || return 2
  count=$(printf '%s' "$count" | tr -d '[:space:]')
  [[ "$count" =~ ^[0-9]+$ ]] || return 2
  if (( count > 0 )); then
    return 0
  fi
  return 1
}

check_one() {
  local name=$1 port=$2 host=$3 action=$4
  local state="$STATE_DIR/$name" failures=0

  if probe "$port" "$host"; then
    /usr/bin/rm -f "$state"
    return 0
  fi

  if [[ -s "$state" ]]; then
    read -r failures < "$state" || failures=0
  fi
  failures=$((failures + 1))
  printf '%s\n' "$failures" > "$state"
  if (( failures < 2 )); then
    log "$name HTTP probe failed once; waiting for a second failure"
    return 0
  fi

  if [[ "$name" == "register" ]]; then
    register_queue_state
    local queue_state=$?
    if (( queue_state == 0 )) && /usr/bin/systemctl is-active --quiet turb-gpt-register.service; then
      log "$name HTTP probe failed $failures times; active registration jobs detected, deferring restart"
      return 0
    fi
    if (( queue_state == 2 )) && /usr/bin/systemctl is-active --quiet turb-gpt-register.service; then
      log "$name HTTP probe failed $failures times; active-job state unknown while service is active, deferring restart"
      return 0
    fi
  fi

  log "$name HTTP probe failed $failures times; running: $action"
  if bash -c "$action"; then
    log "$name recovery command completed"
  else
    log "$name recovery command failed"
  fi
  /usr/bin/rm -f "$state"
}

if ! /usr/bin/systemctl is-active --quiet nginx; then
  log "nginx is not active; restarting nginx"
  /usr/bin/systemctl restart nginx || true
fi

check_one dsh_relay 3082 dsh.feixueapi.xyz \
  "/usr/bin/systemctl restart dsh-harness-muse-relay.service"
check_one register 5100 register.feixueapi.xyz \
  "/usr/bin/systemctl restart turb-gpt-register.service"
check_one monitor 25774 monitor.feixueapi.xyz \
  "/usr/bin/systemctl restart komari.service"
check_one api 8080 api.feixueapi.xyz \
  "/usr/bin/docker restart sub2api"
check_one cpa 8317 cpa.feixueapi.xyz \
  "/usr/bin/docker restart cpa"

# Resin reverse tunnel: keep the public resin.feixueapi.xyz upstream alive after VM restarts.
check_one resin 12260 resin.feixueapi.xyz \
  "/usr/bin/sudo -n -u feixueadmin -H /home/feixueadmin/.local/bin/marriedsh --config /home/feixueadmin/.config/marriedsh/config.toml console --pty never -- systemctl restart resin-main-tunnel.service"
