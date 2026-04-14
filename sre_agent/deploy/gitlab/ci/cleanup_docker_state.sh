#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

if [ -n "${CLEANUP_DOCKER_HOST:-}" ]; then
  export DOCKER_HOST="${CLEANUP_DOCKER_HOST}"
elif [ -n "${PREVIEW_DOCKER_HOST:-}" ]; then
  export DOCKER_HOST="${PREVIEW_DOCKER_HOST}"
fi

cutoff_epoch="$(( $(date +%s) - 7 * 24 * 3600 ))"
managed_label="com.cube_studio.sre_agent.ci.managed=true"
branch_label="com.cube_studio.sre_agent.ci.branch=${SRE_CI_BRANCH:-feature/sre-c-core-infra}"

cleanup_preview_containers_for_kind() {
  local kind="$1"
  local keep_count="$2"
  local ranked_ids=()
  local ranked_id=""
  while read -r ranked_id; do
    [ -n "${ranked_id}" ] || continue
    ranked_ids+=("${ranked_id}")
  done < <(
    docker ps -a \
      --filter "label=${managed_label}" \
      --filter "label=com.cube_studio.sre_agent.ci.kind=${kind}" \
      --filter "label=${branch_label}" \
      --format '{{.ID}} {{.CreatedAt}}' \
      | while read -r container_id created_at_1 created_at_2 created_at_3 created_at_4 created_at_5; do
          [ -n "${container_id}" ] || continue
          created_epoch="$(date -d "${created_at_1} ${created_at_2} ${created_at_3} ${created_at_4} ${created_at_5}" +%s 2>/dev/null || true)"
          [ -n "${created_epoch}" ] || continue
          echo "${created_epoch} ${container_id}"
        done \
      | sort -nr \
      | awk '{print $2}'
  )

  local now_epoch
  now_epoch="$(date +%s)"
  local index=0
  local container_id=""
  local expires_at_epoch=""
  for container_id in "${ranked_ids[@]}"; do
    index=$((index + 1))
    expires_at_epoch="$(docker inspect --format '{{ index .Config.Labels "com.cube_studio.sre_agent.ci.expires_at" }}' "${container_id}" 2>/dev/null || true)"
    if [ -n "${expires_at_epoch}" ] && [ "${expires_at_epoch}" -lt "${now_epoch}" ]; then
      log "removing expired ${kind} container ${container_id}"
      docker rm -f "${container_id}" >/dev/null 2>&1 || true
      continue
    fi
    if [ "${index}" -le "${keep_count}" ]; then
      log "preserving ${kind} container ${container_id}"
      continue
    fi
    log "removing stale ${kind} container ${container_id}"
    docker rm -f "${container_id}" >/dev/null 2>&1 || true
  done
}

cleanup_preview_containers_for_kind preview "${PREVIEW_KEEP_RECENT_COUNT:-3}"
cleanup_preview_containers_for_kind weekly-preview "${WEEKLY_PREVIEW_KEEP_RECENT_COUNT:-1}"

preserve_latest_image_id() {
  local role="$1"
  local pattern="$2"
  docker image ls \
    --filter "label=${managed_label}" \
    --filter "label=com.cube_studio.sre_agent.ci.role=${role}" \
    --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
    | while read -r image_ref image_id; do
        [ -n "${image_ref}" ] || continue
        local tag="${image_ref##*:}"
        if ! [[ "${tag}" =~ ${pattern} ]]; then
          continue
        fi
        if ! epoch_from_tag "${tag}" >/dev/null 2>&1; then
          continue
        fi
        echo "$(epoch_from_tag "${tag}") ${image_id}"
      done \
    | sort -n \
    | tail -n 1 \
    | awk '{print $2}'
}

cleanup_images_for_role() {
  local role="$1"
  shift
  local preserve_ids=()
  local preserve_id=""
  local pattern=""

  for pattern in "$@"; do
    preserve_id="$(preserve_latest_image_id "${role}" "${pattern}")"
    if [ -n "${preserve_id}" ]; then
      preserve_ids+=("${preserve_id}")
    fi
  done

  docker image ls \
    --filter "label=${managed_label}" \
    --filter "label=com.cube_studio.sre_agent.ci.role=${role}" \
    --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
    | while read -r image_ref image_id; do
        [ -n "${image_ref}" ] || continue
        local tag="${image_ref##*:}"
        local image_epoch
        if ! image_epoch="$(epoch_from_tag "${tag}" 2>/dev/null)"; then
          continue
        fi
        for preserve_id in "${preserve_ids[@]}"; do
          if [ "${image_id}" = "${preserve_id}" ]; then
            log "preserving selected ${role} image ${image_ref}"
            continue 2
          fi
        done
        if [ "${image_epoch}" -lt "${cutoff_epoch}" ]; then
          log "removing ${role} image ${image_ref}"
          docker image rm -f "${image_ref}" >/dev/null 2>&1 || true
        fi
      done
}

cleanup_images_for_role base '^base-[0-9]{12}$'
cleanup_images_for_role web '^[0-9a-f]{8}-[0-9]{12}$' '^weekly-[0-9]{12}$' '^[0-9]+\.[0-9]+\.[0-9]+-[0-9]{12}$'

docker builder prune -af --filter "until=168h" >/dev/null 2>&1 || true
docker image prune -af --filter "until=168h" >/dev/null 2>&1 || true
docker container prune -f --filter "until=168h" >/dev/null 2>&1 || true

log "cleanup completed"
