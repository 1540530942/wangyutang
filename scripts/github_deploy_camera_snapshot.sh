#!/usr/bin/env bash
set -euo pipefail

archive_path="${1:?release archive path is required}"
commit_sha="${2:-unknown}"

deploy_root="${CAMERA_DEPLOY_ROOT:-/root/camera_snapshot}"
release_root="${CAMERA_RELEASE_ROOT:-/root/camera_snapshot/releases}"
platform_root="${PLATFORM_ROOT:-/root/control_platform}"
compose_file="${CAMERA_COMPOSE_FILE:-/root/control_platform/infra/docker-compose.platform.yml}"
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

echo "commit_sha=$commit_sha" > "$deploy_root/RELEASE_INFO"
date -Is >> "$deploy_root/RELEASE_INFO"

echo "==> Validating Python files"
cd "$deploy_root"
python3 -m py_compile server.py pi_camera_sender.py

echo "==> Building camera-snapshot:local"
docker build \
  --build-arg PYTHON_IMAGE="${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim}" \
  -t camera-snapshot:local \
  "$deploy_root"

echo "==> Restarting camera-snapshot container"
cd "$platform_root"
docker compose -f "$compose_file" up -d --no-deps --force-recreate camera-snapshot

echo "==> Cloud health check"
for _ in $(seq 1 20); do
  if docker exec camera-snapshot python - <<'PY'
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8099/api/health", timeout=3).read()
PY
  then
    docker ps --filter name=camera-snapshot --format '{{.Names}} {{.Status}}'
    echo "CAMERA_CLOUD_DEPLOY_OK=$commit_sha"
    exit 0
  fi
  sleep 2
done

echo "camera-snapshot did not become healthy" >&2
docker logs --tail 120 camera-snapshot >&2 || true
exit 1
