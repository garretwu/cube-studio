#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

require_env NEXUS_REGISTRY
require_env NEXUS_USERNAME
require_env NEXUS_PASSWORD

IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-sre_agent}"
BASE_IMAGE_NAME="${BASE_IMAGE_NAME:-$(base_image_name_for_lane "$(publish_lane "${PIPELINE_KIND:-commit}")")}"
TAGS_URL="$(registry_tags_api "${BASE_IMAGE_NAME}")"
PYTHON_BIN="$(python_cmd)"

response="$(curl -fsSL -u "${NEXUS_USERNAME}:${NEXUS_PASSWORD}" "${TAGS_URL}")"

latest_tag="$(
  RESPONSE_JSON="${response}" "${PYTHON_BIN}" - <<'PY'
import json
import os
import re

payload = json.loads(os.environ["RESPONSE_JSON"])
tags = payload.get("tags") or []
base_tags = sorted(tag for tag in tags if re.fullmatch(r"base-\d{12}", str(tag)))
print(base_tags[-1] if base_tags else "")
PY
)"

if [ -z "${latest_tag}" ]; then
  echo "no published base image tag found under ${IMAGE_NAMESPACE}/${BASE_NAME}" >&2
  exit 1
fi

echo "${latest_tag}"
