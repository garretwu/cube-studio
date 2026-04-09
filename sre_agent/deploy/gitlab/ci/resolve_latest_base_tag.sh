#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

require_env NEXUS_REGISTRY
require_env NEXUS_USERNAME
require_env NEXUS_PASSWORD

IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-cube-studio}"
BASE_NAME="${BASE_NAME:-sre-agent-base}"
TAGS_URL="$(registry_tags_api "${BASE_NAME}")"

response="$(curl -fsSL -u "${NEXUS_USERNAME}:${NEXUS_PASSWORD}" "${TAGS_URL}")"

latest_tag="$(
  echo "${response}" \
    | jq -r '.tags[]? // empty' \
    | awk '/^base-[0-9]{12}$/' \
    | sort \
    | tail -n 1
)"

if [ -z "${latest_tag}" ]; then
  echo "no published base image tag found under ${IMAGE_NAMESPACE}/${BASE_NAME}" >&2
  exit 1
fi

echo "${latest_tag}"
