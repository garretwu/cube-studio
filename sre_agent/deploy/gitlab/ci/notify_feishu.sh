#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

PYTHON_BIN="$(python_cmd)"

if [ -z "${FEISHU_WEBHOOK_URL:-}" ]; then
  log "FEISHU_WEBHOOK_URL is not configured, skipping Feishu notification"
  exit 0
fi

job_status="${CI_JOB_STATUS:-unknown}"
if [ "${job_status}" != "failed" ]; then
  log "job status is ${job_status}, skipping Feishu failure notification"
  exit 0
fi

pipeline_kind="${PIPELINE_KIND:-commit}"
notify_commit_failures="${FEISHU_NOTIFY_ON_COMMIT_FAILURE:-0}"

if [ "${pipeline_kind}" = "commit" ] && [ "${notify_commit_failures}" != "1" ]; then
  log "commit failure notifications are disabled, skipping Feishu notification"
  exit 0
fi

timestamp_human="$(TZ="${IMAGE_TAG_TIMEZONE:-Asia/Shanghai}" date '+%Y-%m-%d %H:%M:%S %Z')"
project_path="${CI_PROJECT_PATH:-cube-studio/sre_agent}"
branch_name="${CI_COMMIT_REF_NAME:-unknown}"
job_name="${CI_JOB_NAME:-unknown}"
stage_name="${CI_JOB_STAGE:-unknown}"
pipeline_url="${CI_PIPELINE_URL:-}"
job_url="${CI_JOB_URL:-}"
short_sha="${CI_COMMIT_SHORT_SHA:-unknown}"
commit_title="${CI_COMMIT_TITLE:-}"
user_name="${GITLAB_USER_NAME:-system}"
pipeline_link_markdown="${pipeline_url:-N/A}"
job_link_markdown="${job_url:-N/A}"
card_title="SRE Agent Pipeline Failure"
card_template="red"
pipeline_button_label="Open Pipeline"
job_button_label="Open Job"
next_action="Review the failed job log and pipeline context."

if [ -n "${pipeline_url}" ]; then
  pipeline_link_markdown="[Open Pipeline](${pipeline_url})"
fi

if [ -n "${job_url}" ]; then
  job_link_markdown="[Open Job](${job_url})"
fi

case "${stage_name}" in
  build)
    card_title="SRE Agent Build Failure"
    card_template="red"
    job_button_label="Inspect Build Job"
    next_action="Check Docker build logs, base-image resolution, and dependency changes."
    ;;
  cleanup)
    card_title="SRE Agent Cleanup Failure"
    card_template="grey"
    job_button_label="Inspect Cleanup Job"
    next_action="Check Docker daemon access, cleanup retention logic, and stale resource state."
    ;;
  release)
    if [[ "${job_name}" == validate_* ]]; then
      card_title="SRE Agent Validation Failure"
      card_template="orange"
      job_button_label="Inspect Validation Job"
      next_action="Check health probes, /openapi.json, frontend readiness, and selected test cases."
    elif [[ "${job_name}" == publish_* || "${job_name}" == promote_* ]]; then
      card_title="SRE Agent Release Failure"
      card_template="red"
      job_button_label="Inspect Release Job"
      next_action="Check Nexus login, target image tags, push permissions, and release inputs."
    else
      card_title="SRE Agent Release Stage Failure"
      card_template="red"
      job_button_label="Inspect Release Job"
      next_action="Check release-stage job logs and dependent validation artifacts."
    fi
    ;;
  preview)
    card_title="SRE Agent Preview Failure"
    card_template="orange"
    job_button_label="Inspect Preview Job"
    next_action="Check preview Docker host reachability, port allocation, and container health."
    ;;
esac

if [ "${pipeline_kind}" = "weekly_build" ]; then
  card_title="Weekly Build: ${card_title}"
  pipeline_button_label="Open Weekly Pipeline"
elif [ "${pipeline_kind}" = "base_refresh" ]; then
  card_title="Base Refresh: ${card_title}"
  pipeline_button_label="Open Refresh Pipeline"
elif [ "${pipeline_kind}" = "cleanup" ]; then
  card_title="Scheduled Cleanup: ${card_title}"
  pipeline_button_label="Open Cleanup Pipeline"
fi

payload="$(
  PROJECT_PATH="${project_path}" \
  BRANCH_NAME="${branch_name}" \
  PIPELINE_KIND="${pipeline_kind}" \
  STAGE_NAME="${stage_name}" \
  JOB_NAME="${job_name}" \
  JOB_STATUS="${job_status}" \
  SHORT_SHA="${short_sha}" \
  USER_NAME="${user_name}" \
  TIMESTAMP_HUMAN="${timestamp_human}" \
  COMMIT_TITLE="${commit_title:-N/A}" \
  PIPELINE_LINK="${pipeline_link_markdown}" \
  JOB_LINK="${job_link_markdown}" \
  CARD_TITLE="${card_title}" \
  CARD_TEMPLATE="${card_template}" \
  PIPELINE_BUTTON_LABEL="${pipeline_button_label}" \
  JOB_BUTTON_LABEL="${job_button_label}" \
  NEXT_ACTION="${next_action}" \
  "${PYTHON_BIN}" - <<'PY'
import json
import os

payload = {
    "msg_type": "interactive",
    "card": {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": os.environ["CARD_TEMPLATE"],
            "title": {"tag": "plain_text", "content": os.environ["CARD_TITLE"]},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**Project**: {os.environ['PROJECT_PATH']}\n"
                        f"**Branch**: {os.environ['BRANCH_NAME']}\n"
                        f"**Pipeline Kind**: {os.environ['PIPELINE_KIND']}\n"
                        f"**Stage**: {os.environ['STAGE_NAME']}\n"
                        f"**Job**: {os.environ['JOB_NAME']}\n"
                        f"**Status**: {os.environ['JOB_STATUS']}"
                    ),
                },
            },
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Commit**\n{os.environ['SHORT_SHA']}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**User**\n{os.environ['USER_NAME']}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Time**\n{os.environ['TIMESTAMP_HUMAN']}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Commit Title**\n{os.environ['COMMIT_TITLE']}"}},
                ],
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**Suggested Next Action**\n{os.environ['NEXT_ACTION']}"},
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": os.environ["PIPELINE_BUTTON_LABEL"]},
                        "type": "primary",
                        "url": os.environ["PIPELINE_LINK"],
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": os.environ["JOB_BUTTON_LABEL"]},
                        "url": os.environ["JOB_LINK"],
                    },
                ],
            },
        ],
    },
}

print(json.dumps(payload, ensure_ascii=False))
PY
)"

curl -fsS -X POST "${FEISHU_WEBHOOK_URL}" \
  -H "Content-Type: application/json" \
  -d "${payload}" >/dev/null

log "Feishu card notification sent for ${job_name}"
