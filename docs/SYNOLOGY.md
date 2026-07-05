# Deploying to Synology DSM 6.2

This guide walks through deploying the Personal AI Voice Diary to a
Synology NAS running DSM 6.2 (tested on `DSM 6.2-23739`), reachable from
any device on your local network at `http://<nas-ip>:9000/`.

If you want external access (from phones on 4G/5G), add Cloudflare
Tunnel — see the "External access" section at the end.

## Prerequisites

- **DSM 6.2** with admin access
- **Docker package** installed (open Package Center → search "Docker" →
  Install). This gives you the Docker engine + `docker-compose`. The
  SynoCommunity version works fine.
- **SSH enabled** on the NAS (Control Panel → Terminal & SNMP → Enable SSH)
- A DeepSeek API key (https://platform.deepseek.com → API Keys)

## One-time setup

```bash
# 1. SSH into the NAS as your admin user
ssh <admin>@<nas-ip>

# 2. Create the project + data + backup directories
sudo mkdir -p /volume1/docker/diary/data
sudo mkdir -p /volume2/docker-backups/diary
sudo chown -R $(whoami):users /volume1/docker/diary /volume2/docker-backups/diary

# 3. Get the code onto the NAS. Either:
#    a) clone the GitHub repo (cleanest):
sudo -u $(whoami) git clone https://github.com/Grossmind/ai_diary.git /volume1/docker/diary-tmp
sudo mv /volume1/docker/diary-tmp/* /volume1/docker/diary-tmp/.* /volume1/docker/diary/
sudo rmdir /volume1/docker/diary-tmp
#    b) or scp from your Mac:
#       rsync -av --exclude=.venv --exclude=data --exclude=__pycache__ \
#             --exclude=.git --exclude='*.pyc' \
#             /Users/gbs/projects/diary/ <admin>@<nas-ip>:/volume1/docker/diary/

# 4. Create .env on the NAS with your API key
cd /volume1/docker/diary
cat > .env <<'EOF'
DEEPSEEK_API_KEY=sk-your-real-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=9000
LOG_LEVEL=INFO
TZ=Asia/Shanghai
DATA_DIR=/data
EOF
chmod 600 .env

# 5. Build and start the container
docker-compose build
docker-compose up -d

# 6. Verify
curl http://localhost:9000/health
docker-compose logs --tail=20
```

You should see:

```json
{"data":{"status":"ok","service":"diary","version":"0.1.0","env":"production"},"error":null}
```

Open `http://<nas-ip>:9000/` in any browser on your LAN.

## Day-to-day

```bash
# View logs
cd /volume1/docker/diary && docker-compose logs -f

# Restart after config change
cd /volume1/docker/diary && docker-compose restart

# Stop the app (data is preserved on the volume)
cd /volume1/docker/diary && docker-compose down

# Rebuild after pulling new code
cd /volume1/docker/diary && git pull && docker-compose build && docker-compose up -d

# Update the DeepSeek API key
cd /volume1/docker/diary
nano .env
docker-compose restart diary
```

## Backups

A `scripts/backup.sh` ships with the project — it tars the data dir and
drops the file at `/volume2/docker-backups/diary/`. Rotates to keep the
last 30 by default.

### Manual run

```bash
cd /volume1/docker/diary && bash scripts/backup.sh
```

### Automatic daily run via DSM Task Scheduler

1. DSM → **Control Panel** → **Task Scheduler** → **Create** → **Scheduled Task** → **User-defined script**
2. **General** tab:
   - Task name: `diary-backup`
   - User: `root` (so it can read the data dir cleanly)
   - Schedule: Daily, e.g. 03:00
3. **Task Settings** tab → User-defined script:
   ```bash
   bash /volume1/docker/diary/scripts/backup.sh >> /volume1/docker/diary/backups.log 2>&1
   ```
4. OK.

### Restore

```bash
cd /volume1/docker/diary
ls /volume2/docker-backups/diary/   # pick a file
bash scripts/restore.sh /volume2/docker-backups/diary/diary-20260705-120000.tar.gz
```

The script stops the container, replaces the data dir, restarts, and asks
for a `yes` confirmation before doing anything destructive.

## Migrating your existing local diary

If you have an existing `diary.db` on your Mac with entries you want to
keep:

```bash
# On Mac
scp /Users/gbs/projects/diary/data/diary.db* <admin>@<nas-ip>:/volume1/docker/diary/data/

# On NAS
cd /volume1/docker/diary
docker-compose restart diary
```

`diary.db-wal` and `diary.db-shm` may need to be copied too — include them
with the `*` glob. SQLite will replay the WAL on next open.

## External access (optional)

This deploy is local-only by default. To reach it from outside your home
network (e.g., 4G phone):

### Option A — Cloudflare Tunnel (recommended, free)

1. Create a Cloudflare account, add your domain.
2. In DSM, install the `cloudflared` package or run it as a second
   container in your `docker-compose.yml`. Add:

   ```yaml
   services:
     diary: { ... }   # your existing config

     tunnel:
       image: cloudflare/cloudflared:latest
       command: tunnel --no-autoupdate run
       environment:
         - TUNNEL_TOKEN=<from Cloudflare dashboard>
       depends_on:
         - diary
   ```

3. In Cloudflare dashboard, create a tunnel pointing at `http://diary:9000`.
4. HTTPS is handled automatically by Cloudflare.

### Option B — Reverse proxy with a real domain

Point a domain at your home IP and run Caddy/nginx in front of the
container for automatic Let's Encrypt certs. More setup, more to go wrong.

### Option C — VPN

Run WireGuard on the NAS and connect your phone to it. Cleanest from a
privacy standpoint but requires VPN toggle on the phone.

## Troubleshooting

**`docker-compose: command not found`** — DSM 6.2's Docker package should
include compose. If not, run plain `docker run` with the same flags:

```bash
docker build -t voice-diary .
docker run -d --name diary --restart unless-stopped \
    -p 9000:9000 --env-file .env \
    -v /volume1/docker/diary/data:/data \
    -v /volume2/docker-backups/diary:/backups:ro \
    voice-diary:latest
```

**Container keeps restarting** — `docker-compose logs diary` for the error.
Most common: missing `.env` (the container exits if `DEEPSEEK_API_KEY`
is empty), or data dir perms wrong.

**Can't reach `http://<nas-ip>:9000/` from phone** — make sure both
devices are on the same Wi-Fi network. DSM Firewall (Control Panel →
Security → Firewall) may need port 9000 opened if it's enabled.

**Web Speech API doesn't work on phone** — Chrome on Android needs HTTPS
for `getUserMedia` (mic) on most setups. Local HTTP usually works for
`http://192.168.x.x:9000` directly (browser treats LAN IPs as trusted)
but some browser versions are stricter. If it fails, fall back to
external access via Cloudflare Tunnel (HTTPS).