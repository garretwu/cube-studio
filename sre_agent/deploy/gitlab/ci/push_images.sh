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
echo
echo "========== 候选镜像已发布 =========="
echo "Publish Lane : ${PUBLISH_LANE}"
if [ -f "${REPO_ROOT}/dist/build-base.env" ]; then
  echo "Base Image   : ${BASE_IMAGE_REF}"
  echo "Base Pull    : docker pull ${BASE_IMAGE_REF}"
fi
echo "Web Image    : ${WEB_IMAGE_REF}"
echo "Web Pull     : docker pull ${WEB_IMAGE_REF}"
echo "获取方式      : 复制上面的 docker pull 命令即可拉取本次候选镜像。"
echo "使用说明      : preview 对应的页面和这里输出的镜像 tag 一一对应，可用于内部测试和调试复现。"
echo "===================================="
