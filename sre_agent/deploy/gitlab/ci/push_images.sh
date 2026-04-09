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

docker_registry_login

if [ -f "${REPO_ROOT}/dist/base-image.tar" ]; then
  docker load -i "${REPO_ROOT}/dist/base-image.tar"
  log "pushing base image ${BASE_IMAGE_REF}"
  docker push "${BASE_IMAGE_REF}"
  echo "docker pull ${BASE_IMAGE_REF}"
fi

docker load -i "${REPO_ROOT}/dist/web-image.tar"
log "pushing web image ${WEB_IMAGE_REF}"
docker push "${WEB_IMAGE_REF}"
echo "docker pull ${WEB_IMAGE_REF}"
