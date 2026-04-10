#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

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
  jq -nc \
    --arg project_path "${project_path}" \
    --arg branch_name "${branch_name}" \
    --arg pipeline_kind "${pipeline_kind}" \
    --arg stage_name "${stage_name}" \
    --arg job_name "${job_name}" \
    --arg job_status "${job_status}" \
    --arg short_sha "${short_sha}" \
    --arg user_name "${user_name}" \
    --arg timestamp_human "${timestamp_human}" \
    --arg commit_title "${commit_title:-N/A}" \
    --arg pipeline_link "${pipeline_link_markdown}" \
    --arg job_link "${job_link_markdown}" \
    --arg card_title "${card_title}" \
    --arg card_template "${card_template}" \
    --arg pipeline_button_label "${pipeline_button_label}" \
    --arg job_button_label "${job_button_label}" \
    --arg next_action "${next_action}" \
    '{
      msg_type: "interactive",
      card: {
        schema: "2.0",
        config: {
          wide_screen_mode: true
        },
        header: {
          template: $card_template,
          title: {
            tag: "plain_text",
            content: $card_title
          }
        },
        elements: [
          {
            tag: "div",
            text: {
              tag: "lark_md",
              content:
                "**Project**: \($project_path)\n" +
                "**Branch**: \($branch_name)\n" +
                "**Pipeline Kind**: \($pipeline_kind)\n" +
                "**Stage**: \($stage_name)\n" +
                "**Job**: \($job_name)\n" +
                "**Status**: \($job_status)"
            }
          },
          {
            tag: "div",
            fields: [
              {
                is_short: true,
                text: {
                  tag: "lark_md",
                  content: "**Commit**\n\($short_sha)"
                }
              },
              {
                is_short: true,
                text: {
                  tag: "lark_md",
                  content: "**User**\n\($user_name)"
                }
              },
              {
                is_short: true,
                text: {
                  tag: "lark_md",
                  content: "**Time**\n\($timestamp_human)"
                }
              },
              {
                is_short: true,
                text: {
                  tag: "lark_md",
                  content: "**Commit Title**\n\($commit_title)"
                }
              }
            ]
          },
          {
            tag: "div",
            text: {
              tag: "lark_md",
              content: "**Suggested Next Action**\n\($next_action)"
            }
          },
          {
            tag: "action",
            actions: [
              {
                tag: "button",
                text: {
                  tag: "plain_text",
                  content: $pipeline_button_label
                },
                type: "primary",
                url: $pipeline_link
              },
              {
                tag: "button",
                text: {
                  tag: "plain_text",
                  content: $job_button_label
                },
                url: $job_link
              }
            ]
          }
        ]
      }
    }'
)"

curl -fsS -X POST "${FEISHU_WEBHOOK_URL}" \
  -H "Content-Type: application/json" \
  -d "${payload}" >/dev/null

log "Feishu card notification sent for ${job_name}"
