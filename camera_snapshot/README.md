# TurboPi Camera Snapshot

`camera_snapshot` is the robot-platform camera snapshot service. It receives camera frames, screen captures, and face-screen captures from the Raspberry Pi/TurboPi side, then serves the latest images through a small web UI and API.

## Features

- Camera snapshot upload.
- Screen snapshot upload.
- Face snapshot upload from the `smile_face` renderer.
- Continuous upload mode.
- Optional GPIO status reporting.
- Separate latest-frame channels for `camera`, `screen`, and `face`.

## Local Run

```powershell
cd camera_snapshot
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 0.0.0.0 --port 8099
```

Open:

```text
http://127.0.0.1:8099/
```

## Docker

From the repository root:

```powershell
docker compose up --build camera-snapshot
```

Service port:

```text
8099
```

Health check:

```text
GET http://127.0.0.1:8099/api/health
```

## Data Flow

```text
Browser
-> camera_snapshot web page
-> POST /api/capture or /api/stop
-> cloud task state

Raspberry Pi / TurboPi
-> pi_camera_sender.py
-> GET /api/control
-> capture camera/screen/face JPEG
-> POST /api/frame

Browser
-> GET /api/latest?kind=camera|screen|face
-> GET /api/latest.jpg?kind=camera|screen|face
```

## Edge Sender

Picamera2 example:

```bash
python3 pi_camera_sender.py --server http://110.40.154.41:8099 --device-id turbopi-01
```

USB camera example:

```bash
python3 pi_camera_sender.py --server http://110.40.154.41:8099 --backend opencv --camera-index 0
```

Common options:

```bash
--width 640 --height 480 --quality 78
```

## Upload Token

Production deployments should configure an upload token. The container-side path is:

```text
/app/data/.camera_token
```

Pass the same token on the edge side:

```bash
python3 pi_camera_sender.py --server http://110.40.154.41:8099 --token your-secret-token
```

## API

- `GET /api/health`: service health check.
- `GET /api/control`: read the current capture task.
- `GET /api/gpio`: read latest GPIO status.
- `POST /api/gpio`: upload GPIO status.
- `POST /api/capture`: create a capture task.
- `POST /api/stop?kind=camera|screen|face`: stop a channel task.
- `POST /api/frame`: upload a JPEG frame.
- `POST /api/task-status`: upload task status.
- `GET /api/latest?kind=camera|screen|face`: read latest frame metadata.
- `GET /api/latest.jpg?kind=camera|screen|face`: read latest JPEG.
