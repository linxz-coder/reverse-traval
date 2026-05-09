#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/Users/linxiaozhong/development/reverse-travel-good-choice}"
APP_LABEL="${APP_LABEL:-com.linxz.reverse-traval.app}"
TUNNEL_LABEL="${TUNNEL_LABEL:-com.linxz.reverse-traval.tunnel}"
LOCAL_URL="${LOCAL_URL:-http://127.0.0.1:5012/api/holidays}"
LOG_FILE="${LOG_FILE:-${APP_DIR}/.cache/cloudflared.err.log}"
USER_ID="$(id -u)"

cd "$APP_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

before_size=0
if [[ -f "$LOG_FILE" ]]; then
  before_size="$(wc -c < "$LOG_FILE" | tr -d ' ')"
fi

echo "启动本地应用..."
launchctl kickstart -k "gui/${USER_ID}/${APP_LABEL}"

echo "等待本地服务可用..."
for _ in $(seq 1 30); do
  if curl --noproxy '*' -fsS "$LOCAL_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl --noproxy '*' -fsS "$LOCAL_URL" >/dev/null 2>&1; then
  echo "本地服务没有启动成功，请查看：${APP_DIR}/.cache/launchd-app.err.log" >&2
  exit 1
fi

echo "重启 Cloudflare tunnel..."
launchctl kickstart -k "gui/${USER_ID}/${TUNNEL_LABEL}"

echo "等待新 tunnel 地址..."
for _ in $(seq 1 60); do
  if [[ -f "$LOG_FILE" ]]; then
    new_log="$(tail -c +"$((before_size + 1))" "$LOG_FILE" 2>/dev/null || true)"
    url="$(printf "%s\n" "$new_log" | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)"
    if [[ -n "$url" ]]; then
      echo
      echo "Tunnel 地址：$url"
      echo "本地地址：http://127.0.0.1:5012"
      exit 0
    fi
  fi
  sleep 1
done

echo "没有自动识别到新地址，请手动查看日志："
tail -n 80 "$LOG_FILE"
exit 1
