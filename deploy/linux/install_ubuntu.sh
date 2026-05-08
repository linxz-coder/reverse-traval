#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-reverse_travel}"
APP_DIR="${APP_DIR:-/opt/reverse-traval}"
REPO_URL="${REPO_URL:-https://github.com/linxz-coder/reverse-traval.git}"
DOMAIN="${DOMAIN:-hotel.underfitting.com}"
SWAP_SIZE="${SWAP_SIZE:-4G}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  fonts-noto-cjk \
  git \
  nginx \
  python3-pip \
  python3-venv

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

if ! swapon --show | grep -q /swapfile; then
  fallocate -l "${SWAP_SIZE}" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi

if [ ! -d "${APP_DIR}/.git" ]; then
  rm -rf "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

runuser -u "${APP_USER}" -- python3 -m venv "${APP_DIR}/.venv"
runuser -u "${APP_USER}" -- "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
runuser -u "${APP_USER}" -- "${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"

"${APP_DIR}/.venv/bin/python" -m playwright install-deps chromium
runuser -u "${APP_USER}" -- env PLAYWRIGHT_BROWSERS_PATH="${APP_DIR}/.cache/ms-playwright" \
  "${APP_DIR}/.venv/bin/python" -m playwright install chromium

runuser -u "${APP_USER}" -- mkdir -p "${APP_DIR}/.cache" "${APP_DIR}/exports"

install -m 0644 "${APP_DIR}/deploy/linux/reverse-traval.service" /etc/systemd/system/reverse-traval.service
sed "s/server_name hotel\\.underfitting\\.com;/server_name ${DOMAIN};/" \
  "${APP_DIR}/deploy/linux/nginx-hotel.underfitting.com.conf" \
  > /etc/nginx/sites-available/reverse-traval.conf
ln -sf /etc/nginx/sites-available/reverse-traval.conf /etc/nginx/sites-enabled/reverse-traval.conf
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable reverse-traval
systemctl restart reverse-traval

nginx -t
systemctl enable nginx
systemctl restart nginx

systemctl --no-pager --full status reverse-traval
