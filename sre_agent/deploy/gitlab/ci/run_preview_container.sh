#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"
# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/pipeline.env"
# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/build-web.env"

if [ -n "${PREVIEW_DOCKER_HOST:-}" ]; then
  export DOCKER_HOST="${PREVIEW_DOCKER_HOST}"
fi

if ! docker image inspect "${WEB_IMAGE_REF}" >/dev/null 2>&1; then
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner for preview"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

require_local_docker_image "${WEB_IMAGE_REF}"

mkdir -p "${REPO_ROOT}/dist"

preview_suffix="${CI_PIPELINE_IID:-${CI_PIPELINE_ID:-0}}-${CI_JOB_ID:-0}"
container_name="sre-agent-preview-${preview_suffix}"
backend_container_port="${CONTAINER_BACKEND_PORT:-8000}"
frontend_container_port="${CONTAINER_FRONTEND_PORT:-8080}"
preview_ttl_hours="${PREVIEW_TTL_HOURS:-72}"
expires_at_epoch="$(( $(date +%s) + preview_ttl_hours * 3600 ))"
backend_host_port=""
frontend_host_port=""

choose_port() {
  local start="$1"
  local end="$2"
  shuf -i "${start}-${end}" -n 1
}

cleanup_existing() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
}

cleanup_existing

run_attempts=0
while [ "${run_attempts}" -lt 20 ]; do
  run_attempts=$((run_attempts + 1))
  backend_host_port="$(choose_port "${PREVIEW_BACKEND_PORT_START:-28000}" "${PREVIEW_BACKEND_PORT_END:-28999}")"
  frontend_host_port="$(choose_port "${PREVIEW_FRONTEND_PORT_START:-38000}" "${PREVIEW_FRONTEND_PORT_END:-38999}")"

  run_cmd=(
    docker run -d
    --name "${container_name}"
    --label "com.cube_studio.sre_agent.ci.managed=true"
    --label "com.cube_studio.sre_agent.ci.kind=preview"
    --label "com.cube_studio.sre_agent.ci.branch=${CI_COMMIT_REF_NAME:-manual}"
    --label "com.cube_studio.sre_agent.ci.pipeline=${CI_PIPELINE_ID:-local}"
    --label "com.cube_studio.sre_agent.ci.expires_at=${expires_at_epoch}"
    -p "${backend_host_port}:${backend_container_port}"
    -p "${frontend_host_port}:${frontend_container_port}"
  )

  if [ -n "${SRE_OPENAI_API_KEY:-}" ]; then
    run_cmd+=(-e "SRE_OPENAI_API_KEY=${SRE_OPENAI_API_KEY}")
  fi

  run_cmd+=("${WEB_IMAGE_REF}")

  if "${run_cmd[@]}" >/tmp/sre-agent-preview-container.id 2>/tmp/sre-agent-preview-container.err; then
    break
  fi

  cleanup_existing
done

if ! docker ps --format '{{.Names}}' | grep -Fxq "${container_name}"; then
  cat /tmp/sre-agent-preview-container.err >&2 || true
  echo "unable to start preview container after ${run_attempts} attempts" >&2
  exit 1
fi

status="starting"
deadline=$((SECONDS + ${PREVIEW_HEALTHCHECK_TIMEOUT_SECONDS:-240}))
while [ "${SECONDS}" -lt "${deadline}" ]; do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_name}")"
  if [ "${status}" = "healthy" ]; then
    break
  fi
  if [ "${status}" = "unhealthy" ] || [ "${status}" = "exited" ]; then
    docker logs "${container_name}" || true
    exit 1
  fi
  sleep 5
done

if [ "${status}" != "healthy" ]; then
  docker logs "${container_name}" || true
  echo "preview container ${container_name} did not become healthy in time" >&2
  exit 1
fi

preview_host="${PREVIEW_PUBLIC_HOST:-}"
if [ -z "${preview_host}" ]; then
  if [ -n "${PREVIEW_DOCKER_HOST:-}" ]; then
    preview_host="$(echo "${PREVIEW_DOCKER_HOST}" | sed -E 's#^[a-z]+://([^:/]+).*$#\1#')"
  else
    preview_host="127.0.0.1"
    log "PREVIEW_DOCKER_HOST is not set, preview container lives on the job Docker daemon and may not be externally reachable after the job finishes"
  fi
fi

cat > "${REPO_ROOT}/dist/preview.env" <<EOF
PREVIEW_CONTAINER_NAME=${container_name}
PREVIEW_BACKEND_URL=http://${preview_host}:${backend_host_port}
PREVIEW_FRONTEND_URL=http://${preview_host}:${frontend_host_port}
PREVIEW_EXPIRES_AT_EPOCH=${expires_at_epoch}
EOF

log "preview backend: http://${preview_host}:${backend_host_port}"
log "preview frontend: http://${preview_host}:${frontend_host_port}"
log "preview ttl hours: ${preview_ttl_hours}"
