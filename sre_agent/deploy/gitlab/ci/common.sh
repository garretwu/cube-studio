#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[ci] $*"
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "required environment variable is missing: ${name}" >&2
    exit 1
  fi
}

docker_registry_login() {
  require_env NEXUS_REGISTRY
  require_env NEXUS_USERNAME
  require_env NEXUS_PASSWORD
  echo "${NEXUS_PASSWORD}" | docker login "${NEXUS_REGISTRY}" --username "${NEXUS_USERNAME}" --password-stdin
}

registry_scheme() {
  echo "${NEXUS_REGISTRY_SCHEME:-https}"
}

registry_image_ref() {
  local image_name="$1"
  local image_tag="$2"
  local image_namespace="${IMAGE_NAMESPACE:-cube-studio}"
  require_env NEXUS_REGISTRY
  echo "${NEXUS_REGISTRY}/${image_namespace}/${image_name}:${image_tag}"
}

registry_tags_api() {
  local image_name="$1"
  local image_namespace="${IMAGE_NAMESPACE:-cube-studio}"
  require_env NEXUS_REGISTRY
  echo "$(registry_scheme)://${NEXUS_REGISTRY}/v2/${image_namespace}/${image_name}/tags/list"
}

parse_timestamp_from_tag() {
  local tag="$1"
  if [[ "${tag}" =~ ([0-9]{12})$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

epoch_from_tag() {
  local tag="$1"
  local ts
  ts="$(parse_timestamp_from_tag "${tag}")" || return 1
  date -d "${ts:0:4}-${ts:4:2}-${ts:6:2} ${ts:8:2}:${ts:10:2}:00" +%s
}

timestamp_now() {
  TZ="${IMAGE_TAG_TIMEZONE:-Asia/Shanghai}" date +%Y%m%d%H%M
}

safe_slug() {
  echo "$1" | tr '/_ ' '-' | tr -cd '[:alnum:]-'
}

repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd
}

read_default_release_version() {
  local root
  root="$(repo_root)"
  local version_file="${root}/sre_agent/VERSION"
  if [ -f "${version_file}" ]; then
    tr -d '[:space:]' < "${version_file}"
    return 0
  fi
  echo "0.1.0"
}
