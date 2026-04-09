#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

mkdir -p dist

IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-cube-studio}"
BASE_NAME="${BASE_NAME:-sre-agent-base}"
APP_NAME="${APP_NAME:-sre-agent-web}"
TIMESTAMP="$(timestamp_now)"
PIPELINE_KIND="${PIPELINE_KIND:-commit}"
SHORT_SHA="${CI_COMMIT_SHORT_SHA:-manual}"

if [ "${PIPELINE_KIND}" = "weekly_build" ]; then
  WEB_IMAGE_TAG="weekly-${TIMESTAMP}"
else
  WEB_IMAGE_TAG="${SHORT_SHA}-${TIMESTAMP}"
fi

BASE_IMAGE_TAG="base-${TIMESTAMP}"

if [ -n "${NEXUS_REGISTRY:-}" ]; then
  BASE_IMAGE_REF="$(registry_image_ref "${BASE_NAME}" "${BASE_IMAGE_TAG}")"
  WEB_IMAGE_REF="$(registry_image_ref "${APP_NAME}" "${WEB_IMAGE_TAG}")"
else
  BASE_IMAGE_REF="${IMAGE_NAMESPACE}/${BASE_NAME}:${BASE_IMAGE_TAG}"
  WEB_IMAGE_REF="${IMAGE_NAMESPACE}/${APP_NAME}:${WEB_IMAGE_TAG}"
fi

cat > dist/pipeline.env <<EOF
IMAGE_TAG_TIMEZONE=${IMAGE_TAG_TIMEZONE:-Asia/Shanghai}
PIPELINE_KIND=${PIPELINE_KIND}
IMAGE_NAMESPACE=${IMAGE_NAMESPACE}
BASE_NAME=${BASE_NAME}
APP_NAME=${APP_NAME}
IMAGE_TIMESTAMP=${TIMESTAMP}
BASE_IMAGE_TAG=${BASE_IMAGE_TAG}
WEB_IMAGE_TAG=${WEB_IMAGE_TAG}
BASE_IMAGE_REF=${BASE_IMAGE_REF}
WEB_IMAGE_REF=${WEB_IMAGE_REF}
BRANCH_SLUG=$(safe_slug "${CI_COMMIT_REF_NAME:-manual}")
EOF

log "base image tag: ${BASE_IMAGE_TAG}"
log "web image tag: ${WEB_IMAGE_TAG}"
log "metadata written to dist/pipeline.env"
