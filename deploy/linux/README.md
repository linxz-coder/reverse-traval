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
- Nightly cache prewarm timer: `reverse-traval-prewarm.timer`
  - Runs around 02:10 CST with a randomized delay.
  - Always prioritizes 深圳、广州、东莞、惠州、汕尾、北京、上海.
  - Rotates through national and global popular cities.
  - Stops after the configured night window so daytime searches keep capacity.

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

Cloudflare DNS should point `hotel.underfitting.com` to the server public IP.
