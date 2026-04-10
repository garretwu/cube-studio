#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"
# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/pipeline.env"

mkdir -p "${REPO_ROOT}/dist"

if [ -f "${REPO_ROOT}/dist/build-base.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/build-base.env"
fi

if [ -f "${REPO_ROOT}/dist/base-image.tar" ]; then
  docker load -i "${REPO_ROOT}/dist/base-image.tar"
fi

if ! docker image inspect "${BASE_IMAGE_REF}" >/dev/null 2>&1; then
  if latest_base_tag="$("${SCRIPT_DIR}/resolve_latest_base_tag.sh" 2>/dev/null)"; then
    BASE_IMAGE_TAG="${latest_base_tag}"
    BASE_IMAGE_REF="$(registry_image_ref "${BASE_NAME}" "${BASE_IMAGE_TAG}")"
    log "pulling latest published base image ${BASE_IMAGE_REF}"
    docker pull "${BASE_IMAGE_REF}"
  else
    log "no published base image found, bootstrapping a base image in this pipeline"
    "${SCRIPT_DIR}/build_base_image.sh"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/dist/build-base.env"
  fi
fi

docker build \
  -f "${REPO_ROOT}/sre_agent/docker/Dockerfile" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE_REF}" \
  --label "com.cube_studio.sre_agent.ci.managed=true" \
  --label "com.cube_studio.sre_agent.ci.role=web" \
  --label "com.cube_studio.sre_agent.ci.branch=${CI_COMMIT_REF_NAME:-manual}" \
  --label "com.cube_studio.sre_agent.ci.pipeline=${CI_PIPELINE_ID:-local}" \
  -t "${WEB_IMAGE_REF}" \
  "${REPO_ROOT}"

docker save -o "${REPO_ROOT}/dist/web-image.tar" "${WEB_IMAGE_REF}"

cat > "${REPO_ROOT}/dist/build-web.env" <<EOF
WEB_IMAGE_BUILT=1
WEB_IMAGE_REF=${WEB_IMAGE_REF}
WEB_IMAGE_TAG=${WEB_IMAGE_TAG}
BASE_IMAGE_REF=${BASE_IMAGE_REF}
EOF

log "built web image ${WEB_IMAGE_REF}"
