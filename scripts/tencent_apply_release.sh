#!/usr/bin/env bash
set -euo pipefail

release_dir="${1:?release directory is required}"
deploy_root="${2:-/root/wangyutang_platform}"

if [ ! -d "$release_dir" ]; then
  echo "release directory not found: $release_dir" >&2
  exit 1
fi

cd "$release_dir"

echo "==> Validating release files"
test -f docker-compose.yml
test -f robot_gateway/Caddyfile
test -d robot_gateway/site
test -d images

if [ -f SHA256SUMS.txt ]; then
  sha256sum -c SHA256SUMS.txt
fi

echo "==> Loading Docker images"
for archive in images/*.tar; do
  [ -f "$archive" ] || continue
  docker load -i "$archive"
done

echo "==> Installing runtime files"
mkdir -p "$deploy_root"
mkdir -p "$deploy_root/robot_gateway"

cp docker-compose.yml "$deploy_root/docker-compose.yml"
cp robot_gateway/Caddyfile "$deploy_root/robot_gateway/Caddyfile"
rm -rf "$deploy_root/robot_gateway/site"
cp -a robot_gateway/site "$deploy_root/robot_gateway/site"

if [ -f "$deploy_root/.env" ]; then
  echo "Using existing $deploy_root/.env"
elif [ -f .env.local-copy ]; then
  cp .env.local-copy "$deploy_root/.env"
  echo "Created $deploy_root/.env from release local copy"
elif [ -f .env.example ]; then
  cp .env.example "$deploy_root/.env"
  echo "Created $deploy_root/.env from .env.example; review secrets before public use"
else
  touch "$deploy_root/.env"
  echo "Created empty $deploy_root/.env"
fi

ln -sfn "$release_dir" "$deploy_root/current"

echo "==> Starting Compose services"
cd "$deploy_root"
docker compose --env-file .env -f docker-compose.yml config --quiet

echo "==> Stopping containers that use fixed production names"
for name in \
  control-platform-caddy \
  wangyutang-caddy \
  control-platform \
  web-manager \
  paper-learning-system \
  paper-hermes \
  remote-sensing \
  robot-gateway \
  remote-control-mqtt \
  remote-control-api \
  remote-control-web \
  camera-snapshot \
  action-move \
  audio-recognition \
  audio-interact \
  pi5-robot \
  slam-mapping \
  smile-face
do
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null
  fi
done

docker compose --env-file .env -f docker-compose.yml up -d --no-build

echo "==> Local health checks"
curl -fsS http://127.0.0.1/api/health >/dev/null
curl -fsS http://127.0.0.1:8099/api/health >/dev/null
curl -fsS http://127.0.0.1:8094/api/health >/dev/null
curl -fsS http://127.0.0.1:8095/api/health >/dev/null
curl -fsS http://127.0.0.1:8097/api/health >/dev/null
curl -fsS http://127.0.0.1:8093/api/health >/dev/null
curl -fsS http://127.0.0.1:8301/api/health >/dev/null
curl -fsS http://127.0.0.1:8096/api/health >/dev/null

echo "==> Service status"
docker compose --env-file .env -f docker-compose.yml ps

echo "APPLIED_RELEASE=$release_dir"
