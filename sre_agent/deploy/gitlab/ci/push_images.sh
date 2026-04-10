#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"
# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/pipeline.env"

if [ -f "${REPO_ROOT}/dist/build-base.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/build-base.env"
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/build-web.env"

if ! docker image inspect "${WEB_IMAGE_REF}" >/dev/null 2>&1; then
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner before push"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

docker_registry_login

if [ -f "${REPO_ROOT}/dist/build-base.env" ]; then
  require_local_docker_image "${BASE_IMAGE_REF}"
  log "pushing base image ${BASE_IMAGE_REF}"
  docker push "${BASE_IMAGE_REF}"
  echo "docker pull ${BASE_IMAGE_REF}"
fi

require_local_docker_image "${WEB_IMAGE_REF}"
log "pushing web image ${WEB_IMAGE_REF}"
docker push "${WEB_IMAGE_REF}"
echo "docker pull ${WEB_IMAGE_REF}"
