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
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner before release promotion"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

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
echo
echo "========== 正式发布已完成 =========="
echo "Release Tag  : ${release_tag}"
echo "Release Image: ${release_image_ref}"
echo "Image Pull   : docker pull ${release_image_ref}"
echo "获取方式      : 复制上面的 Image Pull 命令即可拉取正式发布镜像。"
echo "使用说明      : 建议将 Release Tag、Image Pull 和对应 pipeline 链接一起同步给团队。"
echo "===================================="
