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

if ! docker image inspect "${BASE_IMAGE_REF}" >/dev/null 2>&1; then
  latest_base_tag="$("${SCRIPT_DIR}/resolve_latest_base_tag.sh")"
  BASE_IMAGE_TAG="${latest_base_tag}"
  BASE_IMAGE_REF="$(registry_image_ref "${BASE_NAME}" "${BASE_IMAGE_TAG}")"
  docker pull "${BASE_IMAGE_REF}"
fi

container_name="sre-agent-business-${CI_PIPELINE_IID:-${CI_PIPELINE_ID:-0}}-${CI_JOB_ID:-0}"
docker rm -f "${container_name}" >/dev/null 2>&1 || true

container_id="$(
  docker create \
    --name "${container_name}" \
    --entrypoint bash \
    "${BASE_IMAGE_REF}" \
    -lc '
      set -euo pipefail
      cd /workspace
      export PYTHONPATH=/workspace
      pytest -q \
        sre_agent/tests/test_start_frontend_backend.py \
        fault_injector/tests/unit/features/scenarios/test_scenarios.py
      python -m fault_injector list-scenarios >/tmp/fault-scenarios.txt
      grep -q "rdma_link_flap" /tmp/fault-scenarios.txt
    '
)"

cleanup() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker cp "${REPO_ROOT}/." "${container_id}:/workspace"
docker start -a "${container_id}"

log "business validation passed using ${BASE_IMAGE_REF}"
