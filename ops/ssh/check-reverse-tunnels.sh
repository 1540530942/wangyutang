#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 ALIAS:PORT [ALIAS:PORT ...]" >&2
  exit 2
fi

timeout_seconds=${SSH_CHECK_TIMEOUT:-8}
case "$timeout_seconds" in
  ''|*[!0-9]*) echo "SSH_CHECK_TIMEOUT must be a positive integer" >&2; exit 2 ;;
  0) echo "SSH_CHECK_TIMEOUT must be greater than zero" >&2; exit 2 ;;
esac

echo "timestamp=$(date -Is) host=$(hostname)"
echo "effective_sshd_keepalive"
sshd_output=
if command -v sshd >/dev/null 2>&1; then
  sshd_output=$(sshd -T 2>/dev/null | grep -E '^(clientaliveinterval|clientalivecountmax|tcpkeepalive)' || true)
elif [ -x /usr/sbin/sshd ]; then
  sshd_output=$(/usr/sbin/sshd -T 2>/dev/null | grep -E '^(clientaliveinterval|clientalivecountmax|tcpkeepalive)' || true)
else
  sshd_output="sshd command unavailable"
fi
if [ -n "$sshd_output" ]; then
  printf '%s\n' "$sshd_output"
else
  echo "effective config unavailable to current user"
fi

echo "close_wait_summary"
ss -tan state close-wait 2>/dev/null | awk 'NR > 1 {print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort | uniq -c | sort -nr || true

overall=0
for item in "$@"; do
  alias_name=${item%%:*}
  port=${item##*:}
  if [ -z "$alias_name" ] || [ "$alias_name" = "$item" ] || ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "invalid target=$item expected=ALIAS:PORT" >&2
    overall=2
    continue
  fi

  echo "target=$alias_name port=$port"
  if ss -lnt "sport = :$port" 2>/dev/null | awk 'NR > 1 {found=1} END {exit !found}'; then
    echo "listener=present"
  else
    echo "listener=missing"
    overall=1
  fi

  if output=$(ssh -o BatchMode=yes -o ConnectTimeout="$timeout_seconds" -o ConnectionAttempts=1 \
      "$alias_name" 'printf "hostname=%s user=%s\n" "$(hostname)" "$(id -un)"' 2>&1); then
    echo "ssh=ok $output"
  else
    code=$?
    echo "ssh=failed exit=$code detail=$output"
    overall=1
  fi
done

exit "$overall"
