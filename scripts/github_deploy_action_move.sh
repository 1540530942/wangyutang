#!/usr/bin/env bash
set -euo pipefail

archive_path="${1:?release archive path is required}"
commit_sha="${2:-unknown}"

platform_root="${PLATFORM_ROOT:-/root/control_platform}"
release_root="${ACTION_MOVE_RELEASE_ROOT:-/root/action_move/releases}"
release_dir="$release_root/$commit_sha"
compose_file="${ACTION_MOVE_COMPOSE_FILE:-}"

if [ ! -f "$archive_path" ]; then
  echo "release archive not found: $archive_path" >&2
  exit 1
fi

echo "==> Preparing action_move release $commit_sha"
mkdir -p "$release_dir" "$platform_root"
rm -rf "$release_dir/work"
mkdir -p "$release_dir/work"
tar -xzf "$archive_path" -C "$release_dir/work"

if [ ! -d "$release_dir/work/action_move" ]; then
  echo "archive does not contain action_move/" >&2
  exit 1
fi

echo "==> Installing action_move source"
rm -rf "$platform_root/action_move"
cp -a "$release_dir/work/action_move" "$platform_root/action_move"
echo "commit_sha=$commit_sha" > "$platform_root/action_move/RELEASE_INFO"
date -Is >> "$platform_root/action_move/RELEASE_INFO"

echo "==> Validating action_move files"
cd "$platform_root"
python3 -m py_compile \
  action_move/server.py \
  action_move/action_move_executor.py \
  action_move/edge_action_poller.py \
  action_move/edge_ros_controller.py \
  action_move/regression_guard.py
python3 -m json.tool action_move/skill_catalog.json >/dev/null

if [ -z "$compose_file" ]; then
  if [ -f "$platform_root/docker-compose.yml" ]; then
    compose_file="$platform_root/docker-compose.yml"
  elif [ -f "$platform_root/infra/docker-compose.platform.yml" ]; then
    compose_file="$platform_root/infra/docker-compose.platform.yml"
  else
    echo "compose file not found under $platform_root" >&2
    exit 1
  fi
fi

compose_args=(-f "$compose_file")
if [ -f "$platform_root/.env" ]; then
  compose_args=(--env-file "$platform_root/.env" "${compose_args[@]}")
fi

echo "==> Building action-move:local"
docker build \
  --build-arg PYTHON_IMAGE="${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim}" \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-}" \
  -t action-move:local \
  "$platform_root/action_move"

echo "==> Restarting action-move container"
docker compose "${compose_args[@]}" config --quiet
network="${ACTION_MOVE_DOCKER_NETWORK:-infra_default}"
data_dir="${ACTION_MOVE_DATA_DIR:-$platform_root/action_move/data}"
mkdir -p "$data_dir"
if docker compose "${compose_args[@]}" config --services | grep -qx 'action-move'; then
  if ! docker compose "${compose_args[@]}" up -d --no-build --no-deps --force-recreate action-move; then
    echo "compose action-move restart failed; recreating standalone container"
    docker rm -f action-move >/dev/null 2>&1 || true
    docker run -d \
      --name action-move \
      --network "$network" \
      --restart unless-stopped \
      -v "$data_dir:/app/data" \
      action-move:local
  fi
else
  echo "compose service action-move not found; recreating standalone container"
  docker rm -f action-move >/dev/null 2>&1 || true
  docker run -d \
    --name action-move \
    --network "$network" \
    --restart unless-stopped \
    -v "$data_dir:/app/data" \
    action-move:local
fi

echo "==> Cloud health check"
for _ in $(seq 1 30); do
  if docker exec action-move python - <<'PY'
import json
import urllib.request

health = json.loads(urllib.request.urlopen("http://127.0.0.1:8094/api/health", timeout=3).read().decode("utf-8"))
skills = json.loads(urllib.request.urlopen("http://127.0.0.1:8094/api/skills", timeout=3).read().decode("utf-8"))
settings = health.get("settings", {})
ids = {skill.get("id") for skill in skills.get("skills", [])}
missing = {"rgb_on", "rgb_off"} - ids
if health.get("status") != "ok":
    raise SystemExit(f"bad health: {health}")
if missing:
    raise SystemExit(f"missing RGB skills: {sorted(missing)}")
for key in ("rgb_red", "rgb_green", "rgb_blue"):
    if key not in settings:
        raise SystemExit(f"missing setting: {key}")
PY
  then
    docker ps --filter name=action-move --format '{{.Names}} {{.Status}}'
    echo "ACTION_MOVE_CLOUD_DEPLOY_OK=$commit_sha"
    exit 0
  fi
  sleep 2
done

echo "action-move did not become healthy" >&2
docker logs --tail 120 action-move >&2 || true
exit 1
