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

if ! docker image inspect "${WEB_IMAGE_REF}" >/dev/null 2>&1; then
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner for preview"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

require_local_docker_image "${WEB_IMAGE_REF}"

preview_docker_host="${PREVIEW_DOCKER_HOST:-}"
preview_target="local"
preview_host="${PREVIEW_PUBLIC_HOST:-}"

if [ -n "${preview_docker_host}" ]; then
  if DOCKER_HOST="${preview_docker_host}" docker info >/dev/null 2>&1; then
    if DOCKER_HOST="${preview_docker_host}" docker image inspect "${WEB_IMAGE_REF}" >/dev/null 2>&1; then
      export DOCKER_HOST="${preview_docker_host}"
      preview_target="remote"
    else
      log "preview docker host ${preview_docker_host} is reachable but does not have ${WEB_IMAGE_REF}, falling back to local docker host"
    fi
  else
    log "preview docker host ${preview_docker_host} is unreachable, falling back to local docker host"
  fi
fi

if [ "${preview_target}" = "local" ]; then
  unset DOCKER_HOST
  unset DOCKER_TLS_CERTDIR
fi

mkdir -p "${REPO_ROOT}/dist"

pipeline_kind="${PIPELINE_KIND:-commit}"
preview_kind_label="preview"
preview_name_prefix="sre-agent-preview"
preview_keep_recent_count="${PREVIEW_KEEP_RECENT_COUNT:-3}"
if [ "${pipeline_kind}" = "weekly_build" ]; then
  preview_kind_label="weekly-preview"
  preview_name_prefix="sre-agent-weekly-preview"
  preview_keep_recent_count="${WEEKLY_PREVIEW_KEEP_RECENT_COUNT:-1}"
fi

preview_suffix="${CI_COMMIT_SHORT_SHA:-manual}-${CI_PIPELINE_IID:-${CI_PIPELINE_ID:-0}}-${CI_JOB_ID:-0}"
container_name="${preview_name_prefix}-${preview_suffix}"
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

cleanup_previous_previews() {
  local keep_existing_count="$1"
  local previous_container_id=""
  while read -r previous_container_id; do
    [ -n "${previous_container_id}" ] || continue
    log "removing previous preview container ${previous_container_id}"
    docker rm -f "${previous_container_id}" >/dev/null 2>&1 || true
  done < <(
    docker ps -a \
      --filter "label=com.cube_studio.sre_agent.ci.managed=true" \
      --filter "label=com.cube_studio.sre_agent.ci.kind=${preview_kind_label}" \
      --filter "label=com.cube_studio.sre_agent.ci.branch=${CI_COMMIT_REF_NAME:-manual}" \
      --format '{{.ID}} {{.CreatedAt}}' \
      | while read -r existing_id created_at_1 created_at_2 created_at_3 created_at_4 created_at_5; do
          [ -n "${existing_id}" ] || continue
          created_epoch="$(date -d "${created_at_1} ${created_at_2} ${created_at_3} ${created_at_4} ${created_at_5}" +%s 2>/dev/null || true)"
          [ -n "${created_epoch}" ] || continue
          echo "${created_epoch} ${existing_id}"
        done \
      | sort -nr \
      | awk '{print $2}' \
      | tail -n +"$((keep_existing_count + 1))"
  )
}

existing_preview_keep_count="$((preview_keep_recent_count - 1))"
if [ "${existing_preview_keep_count}" -lt 0 ]; then
  existing_preview_keep_count=0
fi

cleanup_previous_previews "${existing_preview_keep_count}"
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
    --label "com.cube_studio.sre_agent.ci.kind=${preview_kind_label}"
    --label "com.cube_studio.sre_agent.ci.branch=${CI_COMMIT_REF_NAME:-manual}"
    --label "com.cube_studio.sre_agent.ci.pipeline=${CI_PIPELINE_ID:-local}"
    --label "com.cube_studio.sre_agent.ci.commit=${CI_COMMIT_SHORT_SHA:-manual}"
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

if [ -z "${preview_host}" ]; then
  if [ "${preview_target}" = "remote" ]; then
    preview_host="$(echo "${preview_docker_host}" | sed -E 's#^[a-z]+://([^:/]+).*$#\1#')"
  else
    preview_host="127.0.0.1"
    log "preview container lives on the local job docker daemon and may not be externally reachable after the job finishes"
  fi
fi

cat > "${REPO_ROOT}/dist/preview.env" <<EOF
PREVIEW_CONTAINER_NAME=${container_name}
PREVIEW_BACKEND_URL=http://${preview_host}:${backend_host_port}
PREVIEW_FRONTEND_URL=http://${preview_host}:${frontend_host_port}
PREVIEW_EXPIRES_AT_EPOCH=${expires_at_epoch}
PREVIEW_IMAGE_REF=${WEB_IMAGE_REF}
PREVIEW_IMAGE_PULL=docker pull ${WEB_IMAGE_REF}
PREVIEW_KIND=${preview_kind_label}
EOF

log "preview backend: http://${preview_host}:${backend_host_port}"
log "preview frontend: http://${preview_host}:${frontend_host_port}"
log "preview ttl hours: ${preview_ttl_hours}"

echo
echo "========== 预览环境已就绪 =========="
echo "Frontend URL : http://${preview_host}:${frontend_host_port}"
echo "Backend URL  : http://${preview_host}:${backend_host_port}"
echo "Image Pull   : docker pull ${WEB_IMAGE_REF}"
echo "Container    : ${container_name}"
echo "Preview Kind : ${preview_kind_label}"
echo "TTL Hours    : ${preview_ttl_hours}"
if [ "${preview_target}" = "remote" ]; then
  echo "访问说明      : 请在浏览器中打开 Frontend URL。"
else
  echo "访问说明      : 当前 preview 运行在本地 shell runner 主机上。"
  echo "访问说明      : 只有当 ${preview_host} 对你的机器可达时，浏览器才能直接访问。"
fi
if [ "${preview_kind_label}" = "weekly-preview" ]; then
  echo "保留策略      : 每周稳定预览仅保留最新 1 个实例，供周版本评审与回看。"
else
  echo "保留策略      : 提交预览默认保留最近 ${preview_keep_recent_count} 个实例，避免评审中的版本被新提交立即替换。"
fi
echo "==================================="
