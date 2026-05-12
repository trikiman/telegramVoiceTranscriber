# Deployment Guide

## Target

Oracle Cloud Ubuntu VPS (Ampere A1 free tier recommended: 4 OCPU / 24 GB RAM).

**Host:** `158.101.214.234`
**SSH:** `ssh -i "e:\Projects\vless\oracle_vless_key" ubuntu@158.101.214.234`

## Prerequisites

1. Session file created on your local machine (`python scripts/login.py`)
2. API_ID and API_HASH from https://my.telegram.org

## Quick Deploy

```bash
# 1. SSH into VPS
ssh -i "e:\Projects\vless\oracle_vless_key" ubuntu@158.101.214.234

# 2. Clone repo (or scp it)
sudo git clone <your-repo-url> /opt/tg-voice-transcriber

# 3. Run setup script
sudo bash /opt/tg-voice-transcriber/deploy/setup-vps.sh

# 4. Edit secrets
sudo nano /etc/tg-voice-transcriber/env
# Fill in: TG_VOICE_API_ID, TG_VOICE_API_HASH, TG_VOICE_PHONE

# 5. Copy session file from local machine
# (run this from your LOCAL machine, not the VPS)
scp -i "e:\Projects\vless\oracle_vless_key" .local/userbot.session ubuntu@158.101.214.234:/tmp/userbot.session

# Then on VPS:
sudo mv /tmp/userbot.session /var/lib/tg-voice-transcriber/userbot.session
sudo chown tgbot:tgbot /var/lib/tg-voice-transcriber/userbot.session
sudo chmod 600 /var/lib/tg-voice-transcriber/userbot.session

# 6. Start
sudo systemctl start tg-voice-transcriber
sudo systemctl status tg-voice-transcriber

# 7. Watch logs
sudo journalctl -u tg-voice-transcriber -f
```

## Filesystem Layout

```
/opt/tg-voice-transcriber/          Code + venv (tgbot:tgbot, 0755)
/etc/tg-voice-transcriber/env       Secrets (root:tgbot, 0640)
/var/lib/tg-voice-transcriber/      Session + model cache (tgbot:tgbot, 0700)
  └── userbot.session               Telethon session (tgbot:tgbot, 0600)
  └── .cache/                       HuggingFace model cache
/etc/systemd/system/tg-voice-transcriber.service
```

## Operations

### Check status
```bash
systemctl status tg-voice-transcriber
```

### View logs
```bash
journalctl -u tg-voice-transcriber -f          # live tail
journalctl -u tg-voice-transcriber --since "1h ago"  # last hour
```

### Restart
```bash
sudo systemctl restart tg-voice-transcriber
```

### Stop
```bash
sudo systemctl stop tg-voice-transcriber
```

### Update code
```bash
cd /opt/tg-voice-transcriber
sudo -u tgbot git pull
sudo -u tgbot .venv/bin/pip install -e . -q
sudo systemctl restart tg-voice-transcriber
```

## Re-authentication

If the session expires (you'll see `AUTH_REQUIRED` in logs):

1. Stop the service: `sudo systemctl stop tg-voice-transcriber`
2. On your LOCAL machine, re-run: `python scripts/login.py`
3. Copy the new session file to the VPS (same scp command as above)
4. Fix permissions: `sudo chown tgbot:tgbot /var/lib/tg-voice-transcriber/userbot.session && sudo chmod 600 /var/lib/tg-voice-transcriber/userbot.session`
5. Start: `sudo systemctl start tg-voice-transcriber`

## Backup

Back up these two files together (they're useless apart):
```bash
sudo cp /etc/tg-voice-transcriber/env ~/backup-env
sudo cp /var/lib/tg-voice-transcriber/userbot.session ~/backup-session
```

Store encrypted off-box. The session file is equivalent to full account access.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AUTH_REQUIRED` in logs | Session expired/revoked | Re-authenticate (see above) |
| Restart loop (5 restarts in 5 min) | Persistent error (missing ffmpeg, bad config) | Check `journalctl`, fix root cause, `systemctl reset-failed tg-voice-transcriber` |
| `MemoryMax exceeded` | Model too large for configured limit | Increase `MemoryMax` in service file or use `base` model |
| No transcriptions appearing | Handler not matching | Check logs for `voice_enqueued` events; verify voice notes are in 1-on-1 chats |
| `ffmpeg: command not found` | FFmpeg not installed | `sudo apt install ffmpeg` |
