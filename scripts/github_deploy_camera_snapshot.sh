#!/usr/bin/env bash
set -euo pipefail

archive_path="${1:?release archive path is required}"
commit_sha="${2:-unknown}"

deploy_root="${CAMERA_DEPLOY_ROOT:-/root/camera_snapshot}"
release_root="${CAMERA_RELEASE_ROOT:-/root/camera_snapshot/releases}"
platform_root="${PLATFORM_ROOT:-/root/control_platform}"
compose_file="${CAMERA_COMPOSE_FILE:-}"
release_dir="$release_root/$commit_sha"

if [ ! -f "$archive_path" ]; then
  echo "release archive not found: $archive_path" >&2
  exit 1
fi

echo "==> Preparing release $commit_sha"
mkdir -p "$release_dir" "$deploy_root"
rm -rf "$release_dir/work"
mkdir -p "$release_dir/work"
tar -xzf "$archive_path" -C "$release_dir/work"

echo "==> Installing camera_snapshot source"
mkdir -p "$deploy_root/static" "$deploy_root/systemd"
cp "$release_dir/work/camera_snapshot/server.py" "$deploy_root/server.py"
cp "$release_dir/work/camera_snapshot/pi_camera_sender.py" "$deploy_root/pi_camera_sender.py"
cp "$release_dir/work/camera_snapshot/requirements.txt" "$deploy_root/requirements.txt"
cp "$release_dir/work/camera_snapshot/Dockerfile" "$deploy_root/Dockerfile"
cp "$release_dir/work/camera_snapshot/README.md" "$deploy_root/README.md"
cp "$release_dir/work/camera_snapshot/static/"* "$deploy_root/static/"
cp "$release_dir/work/camera_snapshot/systemd/camera-snapshot-sender.service" "$deploy_root/systemd/camera-snapshot-sender.service"

deploy_smile_face="${DEPLOY_SMILE_FACE:-false}"

if [ "$deploy_smile_face" = "true" ] && [ -d "$release_dir/work/smile_face" ]; then
  echo "==> Installing smile_face source"
  mkdir -p "$platform_root/smile_face"
  rm -rf "$platform_root/smile_face"
  cp -a "$release_dir/work/smile_face" "$platform_root/smile_face"
fi

echo "commit_sha=$commit_sha" > "$deploy_root/RELEASE_INFO"
date -Is >> "$deploy_root/RELEASE_INFO"

echo "==> Validating Python files"
cd "$deploy_root"
python3 -m py_compile server.py pi_camera_sender.py
if [ -f "$platform_root/smile_face/server.py" ]; then
  cd "$platform_root"
  python3 -m py_compile smile_face/server.py smile_face/fb_renderer.py smile_face/face_render.py
fi

echo "==> Building camera-snapshot:local"
docker build \
  --build-arg PYTHON_IMAGE="${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim}" \
  -t camera-snapshot:local \
  "$deploy_root"
if [ "$deploy_smile_face" = "true" ] && [ -f "$platform_root/smile_face/Dockerfile" ]; then
  echo "==> Building smile-face:local"
  docker build \
    --build-arg PYTHON_IMAGE="${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim}" \
    -t smile-face:local \
    "$platform_root/smile_face"
fi

echo "==> Restarting camera-snapshot container"
cd "$platform_root"
if [ -z "$compose_file" ]; then
  if [ -f "$platform_root/docker-compose.yml" ]; then
    compose_file="$platform_root/docker-compose.yml"
  elif [ -f "$platform_root/infra/docker-compose.platform.yml" ]; then
    compose_file="$platform_root/infra/docker-compose.platform.yml"
  else
    echo "camera compose file not found under $platform_root" >&2
    exit 1
  fi
fi

compose_args=(-f "$compose_file")
if [ -f "$platform_root/.env" ]; then
  compose_args=(--env-file "$platform_root/.env" "${compose_args[@]}")
fi

docker compose "${compose_args[@]}" config --quiet
if [ "$deploy_smile_face" = "true" ] && docker compose "${compose_args[@]}" config --services | grep -qx 'smile-face'; then
  docker compose "${compose_args[@]}" up -d --no-build --no-deps --force-recreate smile-face
fi
docker compose "${compose_args[@]}" up -d --no-build --no-deps --force-recreate camera-snapshot

echo "==> Cloud health check"
for _ in $(seq 1 20); do
  if docker exec camera-snapshot python - <<'PY'
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8099/api/health", timeout=3).read()
PY
  then
    if docker ps --filter name=smile-face --format '{{.Names}}' | grep -qx smile-face; then
      docker exec smile-face python - <<'PY'
import urllib.request
response = urllib.request.urlopen("http://127.0.0.1:8096/api/face/render.jpg", timeout=5)
data = response.read(2)
if data != b"\xff\xd8":
    raise SystemExit("smile-face render endpoint did not return JPEG")
PY
    fi
    docker ps --filter name=camera-snapshot --format '{{.Names}} {{.Status}}'
    docker ps --filter name=smile-face --format '{{.Names}} {{.Status}}' || true
    echo "CAMERA_CLOUD_DEPLOY_OK=$commit_sha"
    exit 0
  fi
  sleep 2
done

echo "camera-snapshot did not become healthy" >&2
docker logs --tail 120 camera-snapshot >&2 || true
exit 1
