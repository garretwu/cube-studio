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
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner for runtime validation"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

require_local_docker_image "${WEB_IMAGE_REF}"

container_name="sre-agent-smoke-${CI_PIPELINE_IID:-${CI_PIPELINE_ID:-0}}-${CI_JOB_ID:-0}"
backend_host_port="$(shuf -i "${SMOKE_BACKEND_PORT_START:-29000}-${SMOKE_BACKEND_PORT_END:-29999}" -n 1)"
frontend_host_port="$(shuf -i "${SMOKE_FRONTEND_PORT_START:-39000}-${SMOKE_FRONTEND_PORT_END:-39999}" -n 1)"

cleanup() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
}

trap cleanup EXIT
cleanup

docker run -d \
  --name "${container_name}" \
  --label "com.cube_studio.sre_agent.ci.managed=true" \
  --label "com.cube_studio.sre_agent.ci.kind=smoke" \
  -p "${backend_host_port}:8000" \
  -p "${frontend_host_port}:8080" \
  "${WEB_IMAGE_REF}" >/dev/null

deadline=$((SECONDS + ${SMOKE_HEALTHCHECK_TIMEOUT_SECONDS:-240}))
status="starting"
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
  echo "smoke container did not become healthy" >&2
  exit 1
fi

curl -fsS "http://127.0.0.1:${backend_host_port}/openapi.json" >/tmp/sre-agent-openapi.json
curl -fsS "http://127.0.0.1:${frontend_host_port}/" >/tmp/sre-agent-frontend.html

grep -q '"openapi"' /tmp/sre-agent-openapi.json
grep -qi '<!doctype html' /tmp/sre-agent-frontend.html

log "runtime smoke passed for ${WEB_IMAGE_REF}"
