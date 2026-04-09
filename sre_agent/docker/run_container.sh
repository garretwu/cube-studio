#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/image.env" ]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/image.env"
fi

IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-cube-studio}"
APP_NAME="${APP_NAME:-sre-agent-web}"
IMAGE_TAG="${IMAGE_TAG:-${IMAGE_TIMESTAMP:-}}"
CONTAINER_NAME="${CONTAINER_NAME:-sre-agent-web}"
HOST_BACKEND_PORT="${HOST_BACKEND_PORT:-8000}"
HOST_FRONTEND_PORT="${HOST_FRONTEND_PORT:-8080}"
CONTAINER_BACKEND_PORT="${CONTAINER_BACKEND_PORT:-8000}"
CONTAINER_FRONTEND_PORT="${CONTAINER_FRONTEND_PORT:-8080}"

if [ -n "${IMAGE_REGISTRY}" ]; then
  IMAGE_REF="${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${APP_NAME}:${IMAGE_TAG}"
else
  IMAGE_REF="${IMAGE_NAMESPACE}/${APP_NAME}:${IMAGE_TAG}"
fi

if [ -z "${IMAGE_TAG}" ]; then
  echo "IMAGE_TAG is required. Run build_images.sh first or export IMAGE_TAG."
  exit 1
fi

if [ -z "${SRE_OPENAI_API_KEY:-}" ]; then
  echo "SRE_OPENAI_API_KEY is required"
  exit 1
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
