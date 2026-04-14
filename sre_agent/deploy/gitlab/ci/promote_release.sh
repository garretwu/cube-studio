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

PYTHON_BIN="$(python_cmd)"

if ! docker image inspect "${WEB_IMAGE_REF}" >/dev/null 2>&1; then
  log "web image ${WEB_IMAGE_REF} is not available locally, rebuilding it on this runner before release promotion"
  "${SCRIPT_DIR}/build_web_image.sh"
fi

docker_registry_login
require_local_docker_image "${WEB_IMAGE_REF}"

mkdir -p "${REPO_ROOT}/dist"

default_release_version="$(read_default_release_version)"
release_web_image_name="$(app_image_name_for_lane release)"
release_base_image_name="$(base_image_name_for_lane release)"
release_tags_url="$(registry_tags_api "${release_web_image_name}")"
release_tags_response="$(curl -fsSL -u "${NEXUS_USERNAME}:${NEXUS_PASSWORD}" "${release_tags_url}")"
release_registry_metadata="$(
  RESPONSE_JSON="${release_tags_response}" "${PYTHON_BIN}" - <<'PY'
import json
import os
import re

try:
    payload = json.loads(os.environ.get("RESPONSE_JSON", "{}"))
except json.JSONDecodeError:
    payload = {}

versions: set[tuple[int, int, int]] = set()
for raw_tag in payload.get("tags") or []:
    tag = str(raw_tag)
    match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)-[0-9]{12}", tag)
    if not match:
        continue
    versions.add(tuple(int(part) for part in match.groups()))

if not versions:
    print("LATEST_RELEASE_VERSION=")
    print("NEXT_RELEASE_VERSION=")
else:
    latest = max(versions)
    print(f"LATEST_RELEASE_VERSION={latest[0]}.{latest[1]}.{latest[2]}")
    print(f"NEXT_RELEASE_VERSION={latest[0]}.{latest[1]}.{latest[2] + 1}")
PY
)"
eval "${release_registry_metadata}"

if [ -n "${RELEASE_VERSION:-}" ]; then
  if [ "${RELEASE_VERSION}" = "auto" ] || [ "${RELEASE_VERSION}" = "next" ]; then
    if [ -n "${NEXT_RELEASE_VERSION:-}" ]; then
      release_version="${NEXT_RELEASE_VERSION}"
    else
      release_version="${default_release_version}"
    fi
    release_version_source="RELEASE_VERSION=${RELEASE_VERSION}"
  else
    release_version="${RELEASE_VERSION}"
    release_version_source="RELEASE_VERSION"
  fi
else
  release_version="${default_release_version}"
  release_version_source="sre_agent/VERSION"
fi
validate_release_version "${release_version}"
release_version="$(normalize_release_version "${release_version}")"

release_version_guard="$(
  RELEASE_VERSION="${release_version}" \
  LATEST_RELEASE_VERSION="${LATEST_RELEASE_VERSION:-}" \
  NEXT_RELEASE_VERSION="${NEXT_RELEASE_VERSION:-}" \
  ALLOW_RELEASE_VERSION_REUSE="${ALLOW_RELEASE_VERSION_REUSE:-0}" \
  "${PYTHON_BIN}" - <<'PY'
import os
import re
import sys

def parse(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[._-].*)?", version)
    if not match:
        raise ValueError(f"invalid release version: {version}")
    return tuple(int(part) for part in match.groups())

version = os.environ["RELEASE_VERSION"]
latest = os.environ.get("LATEST_RELEASE_VERSION", "")
next_version = os.environ.get("NEXT_RELEASE_VERSION", "")
allow_reuse = os.environ.get("ALLOW_RELEASE_VERSION_REUSE") == "1"

if not latest:
    sys.exit(0)

candidate_tuple = parse(version)
latest_tuple = parse(latest)
if candidate_tuple <= latest_tuple and not allow_reuse:
    print(f"release version {version} is not greater than latest release version {latest}", file=sys.stderr)
    if next_version:
        print(f"use RELEASE_VERSION={next_version}, RELEASE_VERSION=auto, or set ALLOW_RELEASE_VERSION_REUSE=1 for an intentional rebuild", file=sys.stderr)
    sys.exit(1)
PY
)"
release_timestamp="${RELEASE_TIMESTAMP:-${IMAGE_TIMESTAMP:-$(timestamp_now)}}"
release_tag="${release_version}-${release_timestamp}"
release_image_ref="$(registry_image_ref "${release_web_image_name}" "${release_tag}")"

log "release version: ${release_version} (source: ${release_version_source})"
if [ -n "${LATEST_RELEASE_VERSION:-}" ]; then
  log "latest release version: ${LATEST_RELEASE_VERSION}; next suggested version: ${NEXT_RELEASE_VERSION}"
fi

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
RELEASE_VERSION=${release_version}
RELEASE_VERSION_SOURCE=${release_version_source}
LATEST_RELEASE_VERSION=${LATEST_RELEASE_VERSION:-}
NEXT_RELEASE_VERSION=${NEXT_RELEASE_VERSION:-}
RELEASE_TAG=${release_tag}
RELEASE_BASE_IMAGE_REF=${release_base_image_ref}
RELEASE_WEB_IMAGE_REF=${release_image_ref}
EOF

log "promoted release image ${release_image_ref}"
echo
echo "========== 正式发布已完成 =========="
echo "Version      : ${release_version} (${release_version_source})"
if [ -n "${LATEST_RELEASE_VERSION:-}" ]; then
  echo "Previous     : ${LATEST_RELEASE_VERSION}"
fi
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
