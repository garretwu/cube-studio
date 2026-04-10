#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[ci] $*"
}

python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  echo "python3 or python is required but was not found" >&2
  return 1
}

ensure_ci_tooling() {
  if command -v apk >/dev/null 2>&1; then
    apk add --no-cache bash curl coreutils python3 >/dev/null
  fi

  local missing=()
  local cmd=""
  for cmd in bash curl docker; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
      missing+=("${cmd}")
    fi
  done

  python_cmd >/dev/null

  if [ "${#missing[@]}" -gt 0 ]; then
    echo "required commands are missing: ${missing[*]}" >&2
    exit 1
  fi
}

ensure_docker_access() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi

  if [ -n "${DOCKER_HOST:-}" ]; then
    log "docker is not reachable via DOCKER_HOST=${DOCKER_HOST}, retrying local docker daemon"
    unset DOCKER_HOST
    unset DOCKER_TLS_CERTDIR
  fi

  docker info >/dev/null 2>&1 || {
    echo "docker daemon is not reachable from this runner" >&2
    exit 1
  }
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

require_local_docker_image() {
  local image_ref="$1"
  if docker image inspect "${image_ref}" >/dev/null 2>&1; then
    return 0
  fi
  echo "required local docker image was not found on this runner: ${image_ref}" >&2
  echo "this pipeline expects shell runner jobs to reuse the same Docker host without tar artifacts" >&2
  return 1
}
