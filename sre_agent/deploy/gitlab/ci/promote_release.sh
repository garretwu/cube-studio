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

mkdir -p "${REPO_ROOT}/dist"

release_version="${RELEASE_VERSION:-$(read_default_release_version)}"
release_timestamp="${RELEASE_TIMESTAMP:-${IMAGE_TIMESTAMP:-$(timestamp_now)}}"
release_tag="${release_version}-${release_timestamp}"
release_web_image_name="$(app_image_name_for_lane release)"
release_base_image_name="$(base_image_name_for_lane release)"
release_image_ref="$(registry_image_ref "${release_web_image_name}" "${release_tag}")"

docker tag "${WEB_IMAGE_REF}" "${release_image_ref}"
docker push "${release_image_ref}"

release_base_image_ref=""
release_base_pull=""
if [ -n "${BASE_IMAGE_REF:-}" ]; then
  if ! docker image inspect "${BASE_IMAGE_REF}" >/dev/null 2>&1; then
    docker pull "${BASE_IMAGE_REF}" >/dev/null 2>&1 || true
  fi
  if docker image inspect "${BASE_IMAGE_REF}" >/dev/null 2>&1; then
    release_base_image_ref="$(registry_image_ref "${release_base_image_name}" "${release_tag}")"
    docker tag "${BASE_IMAGE_REF}" "${release_base_image_ref}"
    docker push "${release_base_image_ref}"
    release_base_pull="docker pull ${release_base_image_ref}"
    echo "${release_base_pull}"
  fi
fi

release_web_pull="docker pull ${release_image_ref}"
echo "${release_web_pull}"

cat > "${REPO_ROOT}/dist/release.env" <<EOF
RELEASE_TAG=${release_tag}
RELEASE_BASE_IMAGE_REF=${release_base_image_ref}
RELEASE_BASE_PULL=${release_base_pull}
RELEASE_WEB_IMAGE_REF=${release_image_ref}
RELEASE_WEB_PULL=${release_web_pull}
EOF

log "promoted release image ${release_image_ref}"
echo
echo "========== 正式发布已完成 =========="
echo "Release Tag  : ${release_tag}"
if [ -n "${release_base_image_ref}" ]; then
  echo "Base Release : ${release_base_image_ref}"
  echo "Base Pull    : docker pull ${release_base_image_ref}"
fi
echo "Web Release  : ${release_image_ref}"
echo "Web Pull     : docker pull ${release_image_ref}"
echo "获取方式      : 复制上面的 Base Pull / Web Pull 命令即可拉取正式发布镜像。"
echo "使用说明      : 建议将 Release Tag、镜像获取地址和对应 pipeline 链接一起同步给团队。"
echo "===================================="
