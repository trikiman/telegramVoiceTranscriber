#!/usr/bin/env bash
# VPS setup script for Telegram Voice Transcriber
# Run as root (or with sudo) on the Oracle Ubuntu VPS.
#
# Prerequisites:
#   - Ubuntu 22.04 or 24.04
#   - Internet access (for apt + pip)
#   - The session file already created on your local machine via scripts/login.py
#
# Usage:
#   ssh ubuntu@158.101.214.234
#   sudo bash deploy/setup-vps.sh

set -euo pipefail

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Telegram Voice Transcriber — VPS Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# 1. System packages
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3.11 python3.11-venv python3-pip ffmpeg git

# 2. Create service user
echo "[2/8] Creating service user 'tgbot'..."
if ! id tgbot &>/dev/null; then
    adduser --system --group --home /var/lib/tg-voice-transcriber --shell /usr/sbin/nologin tgbot
fi

# 3. Create directory structure
echo "[3/8] Creating directory structure..."
mkdir -p /opt/tg-voice-transcriber
mkdir -p /etc/tg-voice-transcriber
mkdir -p /var/lib/tg-voice-transcriber

# 4. Clone/copy code
echo "[4/8] Setting up code in /opt/tg-voice-transcriber..."
if [ -d "/opt/tg-voice-transcriber/.git" ]; then
    cd /opt/tg-voice-transcriber
    git pull --ff-only
else
    echo "  → Copy your repo to /opt/tg-voice-transcriber or git clone it there."
    echo "  → Example: git clone <your-repo-url> /opt/tg-voice-transcriber"
    echo "  → Then re-run this script."
    # For now, assume code is already there or will be copied
fi

# 5. Create venv and install
echo "[5/8] Creating venv and installing dependencies..."
cd /opt/tg-voice-transcriber
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip wheel -q
.venv/bin/pip install -e . -q
# Install faster-whisper (Phase 3 dependency)
.venv/bin/pip install "faster-whisper>=1.2,<2.0" -q

# 6. Pre-download whisper model
echo "[6/8] Pre-downloading faster-whisper 'small' model (this may take a few minutes)..."
.venv/bin/python -c "
from faster_whisper import WhisperModel
import os
os.environ.setdefault('HF_HOME', '/var/lib/tg-voice-transcriber/.cache')
model = WhisperModel('small', device='cpu', compute_type='int8')
print('Model loaded successfully.')
"

# 7. Set up env file (template — user must fill in secrets)
echo "[7/8] Setting up environment file..."
if [ ! -f /etc/tg-voice-transcriber/env ]; then
    cat > /etc/tg-voice-transcriber/env << 'ENVEOF'
# Telegram API credentials
TG_VOICE_API_ID=CHANGE_ME
TG_VOICE_API_HASH=CHANGE_ME
TG_VOICE_PHONE=CHANGE_ME

# Session file path
TG_VOICE_SESSION_PATH=/var/lib/tg-voice-transcriber/userbot.session

# Logging
TG_VOICE_LOG_LEVEL=INFO
TG_VOICE_LOG_TRANSCRIPTS=false

# Model cache
HF_HOME=/var/lib/tg-voice-transcriber/.cache
ENVEOF
    echo "  → IMPORTANT: Edit /etc/tg-voice-transcriber/env with your real credentials!"
else
    echo "  → /etc/tg-voice-transcriber/env already exists, skipping."
fi

# 8. Set permissions
echo "[8/8] Setting permissions..."
chown -R tgbot:tgbot /opt/tg-voice-transcriber
chown -R tgbot:tgbot /var/lib/tg-voice-transcriber
chown root:tgbot /etc/tg-voice-transcriber/env
chmod 640 /etc/tg-voice-transcriber/env

# Install systemd unit
cp /opt/tg-voice-transcriber/deploy/tg-voice-transcriber.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tg-voice-transcriber

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Setup complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "Next steps:"
echo "  1. Edit /etc/tg-voice-transcriber/env with your API_ID, API_HASH, PHONE"
echo "  2. Copy your session file to /var/lib/tg-voice-transcriber/userbot.session"
echo "     scp .local/userbot.session ubuntu@this-vps:/var/lib/tg-voice-transcriber/userbot.session"
echo "  3. Fix session permissions:"
echo "     chown tgbot:tgbot /var/lib/tg-voice-transcriber/userbot.session"
echo "     chmod 600 /var/lib/tg-voice-transcriber/userbot.session"
echo "  4. Start the service:"
echo "     systemctl start tg-voice-transcriber"
echo "  5. Check status:"
echo "     systemctl status tg-voice-transcriber"
echo "     journalctl -u tg-voice-transcriber -f"
echo
