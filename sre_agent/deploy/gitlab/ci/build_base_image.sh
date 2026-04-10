#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"
# shellcheck disable=SC1091
source "${REPO_ROOT}/dist/pipeline.env"

mkdir -p "${REPO_ROOT}/dist"

docker build \
  -f "${REPO_ROOT}/sre_agent/docker/Dockerfile.base" \
  --label "com.cube_studio.sre_agent.ci.managed=true" \
  --label "com.cube_studio.sre_agent.ci.role=base" \
  --label "com.cube_studio.sre_agent.ci.branch=${CI_COMMIT_REF_NAME:-manual}" \
  --label "com.cube_studio.sre_agent.ci.pipeline=${CI_PIPELINE_ID:-local}" \
  -t "${BASE_IMAGE_REF}" \
  "${REPO_ROOT}"

cat > "${REPO_ROOT}/dist/build-base.env" <<EOF
BASE_IMAGE_BUILT=1
BASE_IMAGE_REF=${BASE_IMAGE_REF}
BASE_IMAGE_TAG=${BASE_IMAGE_TAG}
EOF

log "built base image ${BASE_IMAGE_REF} on local docker host"
