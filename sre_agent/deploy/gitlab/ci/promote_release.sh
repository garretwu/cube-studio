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

docker_registry_login
require_local_docker_image "${WEB_IMAGE_REF}"

release_version="${RELEASE_VERSION:-$(read_default_release_version)}"
release_timestamp="${RELEASE_TIMESTAMP:-${IMAGE_TIMESTAMP:-$(timestamp_now)}}"
release_tag="${release_version}-${release_timestamp}"
release_image_ref="$(registry_image_ref "${APP_NAME}" "${release_tag}")"

docker tag "${WEB_IMAGE_REF}" "${release_image_ref}"
docker push "${release_image_ref}"

echo "docker pull ${release_image_ref}"
log "promoted release image ${release_image_ref}"
