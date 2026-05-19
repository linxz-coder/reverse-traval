# Linux Deployment

Recommended target:

- Ubuntu 24.04
- 2 vCPU / 4 GB RAM or higher
- 70 GB system disk
- Open inbound TCP 80 and 443 in the server firewall/security group

Run as root on the server:

```bash
curl -fsSL https://raw.githubusercontent.com/linxz-coder/reverse-traval/main/deploy/linux/install_ubuntu.sh -o /tmp/install_reverse_traval.sh
bash /tmp/install_reverse_traval.sh
```

The app runs behind Nginx:

- App: `127.0.0.1:5012`
- Public domain: `hotel.underfitting.com`
- Service: `reverse-traval`
- Shared cache env: `/etc/reverse-traval/shared-cache.env`
- Nightly cache prewarm timer: `reverse-traval-prewarm.timer`
  - Runs during off-hours around 00:10, 02:10, 04:10, 06:10, and 22:10 CST with a randomized delay.
  - Always prioritizes 深圳、广州.
  - Rotates a larger batch of national and global popular cities to reduce repeated fixed-city work.
  - Warms the first 3 upcoming holidays.
  - Warms both base search cache and administrative/4-star coverage supplement cache.
  - Stops after the configured off-hour window so daytime searches keep capacity.

Useful commands:

```bash
systemctl status reverse-traval
journalctl -u reverse-traval -f
systemctl restart reverse-traval
systemctl status reverse-traval-prewarm.timer
journalctl -u reverse-traval-prewarm.service -n 80
nginx -t
systemctl reload nginx
```

SSH login notes are recorded in [`SERVER_LOGIN.md`](SERVER_LOGIN.md).

Cloudflare DNS should point `hotel.underfitting.com` to the server public IP.

To share cache with the Mac tunnel, configure the same MySQL host/user/password in both:

- Server: `/etc/reverse-traval/shared-cache.env`
- Mac/tunnel: `/Users/linxiaozhong/development/reverse-travel-good-choice/.env.shared-cache`

The app still reads local `.cache` first. If the shared database is unreachable, searches continue normally with local cache and live queries.
