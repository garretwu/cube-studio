#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-cube-studio}"
BASE_NAME="${BASE_NAME:-sre-agent-base}"
APP_NAME="${APP_NAME:-sre-agent-web}"
IMAGE_TIMESTAMP="${IMAGE_TIMESTAMP:-$(date +%Y%m%d%H%M)}"

if [ -n "${IMAGE_REGISTRY}" ]; then
  IMAGE_PREFIX="${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}"
else
  IMAGE_PREFIX="${IMAGE_NAMESPACE}"
fi

BASE_IMAGE="${IMAGE_PREFIX}/${BASE_NAME}"
APP_IMAGE="${IMAGE_PREFIX}/${APP_NAME}"
BASE_TAGGED="${BASE_IMAGE}:${IMAGE_TIMESTAMP}"
APP_TAGGED="${APP_IMAGE}:${IMAGE_TIMESTAMP}"

docker build -f "${REPO_ROOT}/sre_agent/docker/Dockerfile.base" -t "${BASE_TAGGED}" "${REPO_ROOT}"

docker build \
  -f "${REPO_ROOT}/sre_agent/docker/Dockerfile" \
  --build-arg BASE_IMAGE="${BASE_TAGGED}" \
  -t "${APP_TAGGED}" \
  "${REPO_ROOT}"

cat > "${SCRIPT_DIR}/image.env" <<EOF
IMAGE_REGISTRY=${IMAGE_REGISTRY}
IMAGE_NAMESPACE=${IMAGE_NAMESPACE}
BASE_NAME=${BASE_NAME}
APP_NAME=${APP_NAME}
IMAGE_TIMESTAMP=${IMAGE_TIMESTAMP}
BASE_IMAGE=${BASE_IMAGE}
APP_IMAGE=${APP_IMAGE}
BASE_TAGGED=${BASE_TAGGED}
APP_TAGGED=${APP_TAGGED}
EOF

echo "built ${BASE_TAGGED}"
echo "built ${APP_TAGGED}"
echo "IMAGE_TIMESTAMP=${IMAGE_TIMESTAMP}"
echo "metadata written to ${SCRIPT_DIR}/image.env"
