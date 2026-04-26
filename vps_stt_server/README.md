# Jarvis STT Server

Minimal speech-to-text server for Telegram voice messages.

## VPS setup

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg
cd vps_stt_server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export STT_TOKEN="change-this-token"
export STT_MODEL="small"
uvicorn server:app --host 0.0.0.0 --port 8008
```

Use a reverse proxy with HTTPS if the server is exposed to the internet.

## Bot `.env`

```env
STT_PROVIDER=server
STT_SERVER_URL=https://your-domain.example/transcribe
STT_SERVER_TOKEN=change-this-token
STT_SERVER_TIMEOUT=120
```

For direct IP testing:

```env
STT_SERVER_URL=http://your-vps-ip:8008/transcribe
```

Models: `tiny`, `base`, `small`, `medium`, `large-v3`. CPU VPS usually works best with `small` or `base`.
