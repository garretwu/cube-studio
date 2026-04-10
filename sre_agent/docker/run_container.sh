#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG_PATH="${REPO_ROOT}/sre_agent/conf/config.yaml"
if [ -f "${SCRIPT_DIR}/image.env" ]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/image.env"
fi

IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-cube-studio}"
APP_NAME="${APP_NAME:-sre-agent-web}"
IMAGE_TAG="${IMAGE_TAG:-${IMAGE_TIMESTAMP:-}}"
CONTAINER_NAME="${CONTAINER_NAME:-sre-agent-web}"
HOST_BACKEND_PORT="${HOST_BACKEND_PORT:-28000}"
HOST_FRONTEND_PORT="${HOST_FRONTEND_PORT:-28080}"
CONTAINER_BACKEND_PORT="${CONTAINER_BACKEND_PORT:-8000}"
CONTAINER_FRONTEND_PORT="${CONTAINER_FRONTEND_PORT:-8080}"
PORT_SEARCH_LIMIT="${PORT_SEARCH_LIMIT:-50}"

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | grep -q .
    return $?
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${port}$"
    return $?
  fi
  return 1
}

find_available_port() {
  local preferred_port="$1"
  local label="$2"
  local candidate="${preferred_port}"
  local max_port=$((preferred_port + PORT_SEARCH_LIMIT))

  while [ "${candidate}" -le "${max_port}" ]; do
    if ! port_in_use "${candidate}"; then
      if [ "${candidate}" != "${preferred_port}" ]; then
        echo "[warn] ${label} port ${preferred_port} is busy, switched to ${candidate}" >&2
      fi
      echo "${candidate}"
      return 0
    fi
    candidate=$((candidate + 1))
  done

  echo "unable to find an available ${label} port in range ${preferred_port}-${max_port}" >&2
  return 1
}

if [ -n "${IMAGE_REGISTRY}" ]; then
  IMAGE_REF="${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${APP_NAME}:${IMAGE_TAG}"
else
  IMAGE_REF="${IMAGE_NAMESPACE}/${APP_NAME}:${IMAGE_TAG}"
fi

if [ -z "${IMAGE_TAG}" ]; then
  echo "IMAGE_TAG is required. Run build_images.sh first or export IMAGE_TAG."
  exit 1
fi

if [ -z "${SRE_OPENAI_API_KEY:-}" ] && [ -f "${CONFIG_PATH}" ]; then
  config_api_key="$(sed -n 's/^[[:space:]]*api_key:[[:space:]]*"\(.*\)".*$/\1/p' "${CONFIG_PATH}" | head -n 1)"
  if [ -n "${config_api_key:-}" ]; then
    SRE_OPENAI_API_KEY="${config_api_key}"
    echo "SRE_OPENAI_API_KEY is not set, using llm.api_key from ${CONFIG_PATH}"
  fi
fi

if [ -z "${SRE_OPENAI_API_KEY:-}" ]; then
  echo "SRE_OPENAI_API_KEY is required, and no llm.api_key fallback was found in ${CONFIG_PATH}"
  exit 1
fi

HOST_BACKEND_PORT="$(find_available_port "${HOST_BACKEND_PORT}" "backend")"
HOST_FRONTEND_PORT="$(find_available_port "${HOST_FRONTEND_PORT}" "frontend")"

if [ "${HOST_FRONTEND_PORT}" = "${HOST_BACKEND_PORT}" ]; then
  HOST_FRONTEND_PORT="$(find_available_port "$((HOST_FRONTEND_PORT + 1))" "frontend")"
fi

docker run -d --rm \
  --name "${CONTAINER_NAME}" \
  -p "${HOST_BACKEND_PORT}:${CONTAINER_BACKEND_PORT}" \
  -p "${HOST_FRONTEND_PORT}:${CONTAINER_FRONTEND_PORT}" \
  -e "SRE_OPENAI_API_KEY=${SRE_OPENAI_API_KEY}" \
  -e "BACKEND_PORT=${CONTAINER_BACKEND_PORT}" \
  -e "FRONTEND_PORT=${CONTAINER_FRONTEND_PORT}" \
  "${IMAGE_REF}"

echo "started ${CONTAINER_NAME} with image ${IMAGE_REF}"
echo "backend: http://127.0.0.1:${HOST_BACKEND_PORT}"
echo "frontend: http://127.0.0.1:${HOST_FRONTEND_PORT}"
