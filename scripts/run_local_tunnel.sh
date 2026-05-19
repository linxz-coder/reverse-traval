#!/usr/bin/env bash
set -euo pipefail

cd /Users/lxz/Documents/Codex/2026-05-16/new-chat/reverse-traval
export HOME=/Users/lxz
export PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin

exec .cache/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:5012
