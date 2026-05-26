# Smile Face

`smile_face` is a synchronized robot expression screen for the Wangyutang platform. It serves a Wall-E-like Canvas face that can run in a normal browser and in full-screen Chromium kiosk mode on the Raspberry Pi Waveshare LCD.

## Features

- Emotions: `neutral`, `happy`, `joy`, `sad`, `angry`
- Random and forced blinking
- Mouth-open and speaking animation
- Shared backend state so the web page and Raspberry Pi LCD stay synchronized
- Display mode with `?display=1` for full-screen LCD use

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8096 --reload
```

Open:

```text
http://127.0.0.1:8096/
http://127.0.0.1:8096/?display=1
```

## API

```text
GET  /api/health
GET  /api/state
POST /api/face/emotion
POST /api/face/speak
POST /api/face/mouth
POST /api/face/blink
POST /api/face/reset
POST /api/face/{emotion}
```

Example:

```bash
curl -X POST http://127.0.0.1:8096/api/face/emotion \
  -H 'Content-Type: application/json' \
  -d '{"emotion":"happy","intensity":0.9,"source":"curl","message":"happy"}'
```

```bash
curl -X POST http://127.0.0.1:8096/api/face/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，我是瓦力表情屏","emotion":"joy","duration_ms":3000}'
```

## Raspberry Pi LCD

Verified target:

```bash
ssh raspberrypi-via-tencent
```

The Raspberry Pi has `chromium-browser`, a Wayfire session, `/dev/fb0`, and a Waveshare touchscreen.

Preferred LCD path on this Pi is the PyQt full-screen client because it creates a real foreground desktop window and polls the same state API:

```bash
DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus python3 qt_lcd_client.py
```

A framebuffer renderer is also included for non-composited sessions:

```bash
python3 fb_renderer.py
```

The browser kiosk page is also available when the compositor maps Chromium correctly:

```bash
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 chromium-browser --kiosk --app=http://127.0.0.1:8096/?display=1 --noerrdialogs --disable-infobars
```

Use the local URL on the robot for offline-safe operation. If the gateway service is preferred, use:

```bash
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 chromium-browser --kiosk --app=https://www.wangyutang.cn/face/?display=1 --noerrdialogs --disable-infobars
```

## Docker

```bash
docker compose up --build smile-face
```

## Limitation

This first version intentionally uses a browser Canvas renderer instead of direct SPI/framebuffer/LVGL output. Add a hardware renderer later only after the exact LCD model and interface require it.
