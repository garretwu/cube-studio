#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

PYTHON_BIN="$(python_cmd)"

if [ -z "${FEISHU_WEBHOOK_URL:-}" ]; then
  log "FEISHU_WEBHOOK_URL is not configured, skipping Feishu notification"
  exit 0
fi

if [ -f "${REPO_ROOT}/dist/pipeline.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/pipeline.env"
fi

if [ -f "${REPO_ROOT}/dist/preview.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/preview.env"
fi

if [ -f "${REPO_ROOT}/dist/publish.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/publish.env"
fi

if [ -f "${REPO_ROOT}/dist/release.env" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/dist/release.env"
fi

job_status="${CI_JOB_STATUS:-unknown}"
pipeline_kind="${PIPELINE_KIND:-commit}"
job_name="${CI_JOB_NAME:-unknown}"
stage_name="${CI_JOB_STAGE:-unknown}"
notify_commit_failures="${FEISHU_NOTIFY_ON_COMMIT_FAILURE:-0}"
notify_success="${FEISHU_NOTIFY_ON_SUCCESS:-1}"
timestamp_human="$(TZ="${IMAGE_TAG_TIMEZONE:-Asia/Shanghai}" date '+%Y-%m-%d %H:%M:%S %Z')"
project_path="${CI_PROJECT_PATH:-cube-studio/sre_agent}"
branch_name="${CI_COMMIT_REF_NAME:-unknown}"
pipeline_url="${CI_PIPELINE_URL:-}"
job_url="${CI_JOB_URL:-}"
short_sha="${CI_COMMIT_SHORT_SHA:-unknown}"
commit_title="${CI_COMMIT_TITLE:-}"
user_name="${GITLAB_USER_NAME:-system}"
preview_image_pull="docker pull ${PREVIEW_IMAGE_REF:-${WEB_IMAGE_REF:-N/A}}"
published_web_pull_cmd="docker pull ${PUBLISHED_WEB_IMAGE_REF:-${WEB_IMAGE_REF:-N/A}}"
published_base_pull_cmd="N/A"
if [ -n "${PUBLISHED_BASE_IMAGE_REF:-}" ]; then
  published_base_pull_cmd="docker pull ${PUBLISHED_BASE_IMAGE_REF}"
fi
release_web_pull_cmd="docker pull ${RELEASE_WEB_IMAGE_REF:-N/A}"
release_base_pull_cmd="N/A"
if [ -n "${RELEASE_BASE_IMAGE_REF:-}" ]; then
  release_base_pull_cmd="docker pull ${RELEASE_BASE_IMAGE_REF}"
fi

send_card() {
  local payload="$1"
  local response=""
  response="$(
    curl -fsS -X POST "${FEISHU_WEBHOOK_URL}" \
      -H "Content-Type: application/json" \
      -d "${payload}"
  )"

  FEISHU_RESPONSE="${response}" "${PYTHON_BIN}" - <<'PY'
import json
import os
import sys

raw = os.environ.get("FEISHU_RESPONSE", "").strip()
if not raw:
    print("empty response from Feishu webhook", file=sys.stderr)
    sys.exit(1)

try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print(f"unexpected Feishu response: {raw}", file=sys.stderr)
    sys.exit(1)

code = payload.get("code")
if code not in (0, "0"):
    print(f"Feishu webhook rejected the message: {raw}", file=sys.stderr)
    sys.exit(1)
PY
}

failure_payload() {
  local card_title="$1"
  local card_template="$2"
  local pipeline_button_label="$3"
  local job_button_label="$4"
  local next_action="$5"

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
  PIPELINE_URL="${pipeline_url:-}" \
  JOB_URL="${job_url:-}" \
  CARD_TITLE="${card_title}" \
  CARD_TEMPLATE="${card_template}" \
  PIPELINE_BUTTON_LABEL="${pipeline_button_label}" \
  JOB_BUTTON_LABEL="${job_button_label}" \
  NEXT_ACTION="${next_action}" \
  "${PYTHON_BIN}" - <<'PY'
import json
import os

def button(label: str, url: str, primary: bool = False):
    item = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
    }
    if primary:
        item["type"] = "primary"
    if url:
        item["url"] = url
    return item

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
                    button(os.environ["PIPELINE_BUTTON_LABEL"], os.environ["PIPELINE_URL"], primary=True),
                    button(os.environ["JOB_BUTTON_LABEL"], os.environ["JOB_URL"]),
                ],
            },
        ],
    },
}

print(json.dumps(payload, ensure_ascii=False))
PY
}

success_payload() {
  local card_title="$1"
  local card_template="$2"
  local summary_title="$3"
  local hint_text="$4"
  local summary_md="$5"
  local actions_json="$6"

  PROJECT_PATH="${project_path}" \
  BRANCH_NAME="${branch_name}" \
  PIPELINE_KIND="${pipeline_kind}" \
  JOB_NAME="${job_name}" \
  SHORT_SHA="${short_sha}" \
  USER_NAME="${user_name}" \
  TIMESTAMP_HUMAN="${timestamp_human}" \
  COMMIT_TITLE="${commit_title:-N/A}" \
  CARD_TITLE="${card_title}" \
  CARD_TEMPLATE="${card_template}" \
  SUMMARY_TITLE="${summary_title}" \
  HINT_TEXT="${hint_text}" \
  SUMMARY_MD="${summary_md}" \
  ACTIONS_JSON="${actions_json}" \
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
                        f"**Job**: {os.environ['JOB_NAME']}"
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
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{os.environ['SUMMARY_TITLE']}**"},
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": os.environ["SUMMARY_MD"]},
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": os.environ["HINT_TEXT"]},
                ],
            },
            {
                "tag": "action",
                "actions": json.loads(os.environ["ACTIONS_JSON"]),
            },
        ],
    },
}

print(json.dumps(payload, ensure_ascii=False))
PY
}

notify_failure() {
  local card_title="SRE Agent Pipeline Failure"
  local card_template="red"
  local pipeline_button_label="Open Pipeline"
  local job_button_label="Open Job"
  local next_action="Review the failed job log and pipeline context."

  case "${stage_name}" in
    build)
      card_title="SRE Agent Build Failure"
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
        job_button_label="Inspect Release Job"
        next_action="Check Nexus login, target image tags, push permissions, and release inputs."
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

  if [ "${pipeline_kind}" = "commit" ] && [ "${notify_commit_failures}" != "1" ]; then
    log "commit failure notifications are disabled, skipping Feishu notification"
    return 0
  fi

  send_card "$(failure_payload "${card_title}" "${card_template}" "${pipeline_button_label}" "${job_button_label}" "${next_action}")"
  log "Feishu card notification sent for ${job_name}"
}

notify_success() {
  local card_title=""
  local summary_title=""
  local hint_text=""
  local summary_md=""
  local actions_json=""
  local card_template="green"

  case "${job_name}" in
    preview_sre_agent_web|weekly_preview_sre_agent_web)
      card_title="预览环境已就绪"
      summary_title="访问与镜像信息"
      hint_text="可直接通过 Open Preview 打开页面，日志摘要仍会保留在对应 job 中。"
      card_template="blue"
      if [ "${job_name}" = "weekly_preview_sre_agent_web" ]; then
        card_title="每周稳定预览已就绪"
        hint_text="这是 weekly build 生成的稳定预览入口，适合周版本评审与集中演示。"
        card_template="indigo"
      fi
      summary_md=$(
        cat <<EOF
**Frontend URL**
${PREVIEW_FRONTEND_URL:-N/A}

**Backend URL**
${PREVIEW_BACKEND_URL:-N/A}

**Image Pull**
${preview_image_pull}

**Preview Kind**
${PREVIEW_KIND:-preview}
EOF
      )
      actions_json="$(
        PIPELINE_URL="${pipeline_url:-}" \
        JOB_URL="${job_url:-}" \
        FRONTEND_URL="${PREVIEW_FRONTEND_URL:-}" \
        "${PYTHON_BIN}" - <<'PY'
import json, os
actions = []
if os.environ.get("FRONTEND_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Preview"},
        "type": "primary",
        "url": os.environ["FRONTEND_URL"],
    })
if os.environ.get("PIPELINE_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Pipeline"},
        "url": os.environ["PIPELINE_URL"],
    })
if os.environ.get("JOB_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Job"},
        "url": os.environ["JOB_URL"],
    })
print(json.dumps(actions, ensure_ascii=False))
PY
      )"
      ;;
    publish_preview_snapshot)
      card_title="候选镜像已发布"
      summary_title="镜像获取信息"
      hint_text="可直接复制 docker pull 命令给测试或联调同学，preview 页面与镜像 tag 一一对应。"
      card_template="turquoise"
      if [ "${PUBLISHED_LANE:-preview}" = "weekly" ]; then
        card_title="每周候选镜像已发布"
        hint_text="这是 weekly build 产出的候选镜像，可配合 weekly preview 一起评审。"
        card_template="carmine"
      fi
      summary_md=$(
        cat <<EOF
**Publish Lane**
${PUBLISHED_LANE:-${PUBLISH_LANE:-preview}}

**Preview URL**
${PREVIEW_FRONTEND_URL:-N/A}

**Web Pull**
${published_web_pull_cmd}

**Base Pull**
${published_base_pull_cmd}
EOF
      )
      actions_json="$(
        PIPELINE_URL="${pipeline_url:-}" \
        JOB_URL="${job_url:-}" \
        FRONTEND_URL="${PREVIEW_FRONTEND_URL:-}" \
        "${PYTHON_BIN}" - <<'PY'
import json, os
actions = []
if os.environ.get("FRONTEND_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Preview"},
        "type": "primary",
        "url": os.environ["FRONTEND_URL"],
    })
if os.environ.get("PIPELINE_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Pipeline"},
        "url": os.environ["PIPELINE_URL"],
    })
if os.environ.get("JOB_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Job"},
        "url": os.environ["JOB_URL"],
    })
print(json.dumps(actions, ensure_ascii=False))
PY
      )"
      ;;
    promote_sre_agent_release)
      card_title="正式发布已完成"
      summary_title="正式版本信息"
      hint_text="建议将 Release Tag、镜像获取地址与 pipeline 链接一并同步给团队。"
      card_template="green"
      summary_md=$(
        cat <<EOF
**Release Tag**
${RELEASE_TAG:-N/A}

**Web Pull**
${release_web_pull_cmd}

**Base Pull**
${release_base_pull_cmd}
EOF
      )
      actions_json="$(
        PIPELINE_URL="${pipeline_url:-}" \
        JOB_URL="${job_url:-}" \
        "${PYTHON_BIN}" - <<'PY'
import json, os
actions = []
if os.environ.get("PIPELINE_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Pipeline"},
        "type": "primary",
        "url": os.environ["PIPELINE_URL"],
    })
if os.environ.get("JOB_URL"):
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Open Job"},
        "url": os.environ["JOB_URL"],
    })
print(json.dumps(actions, ensure_ascii=False))
PY
      )"
      ;;
    *)
      log "job ${job_name} is not configured for Feishu success notification, skipping"
      return 0
      ;;
  esac

  if [ "${notify_success}" != "1" ]; then
    log "success notifications are disabled, skipping Feishu notification"
    return 0
  fi

  send_card "$(success_payload "${card_title}" "${card_template}" "${summary_title}" "${hint_text}" "${summary_md}" "${actions_json}")"
  log "Feishu success card sent for ${job_name}"
}

case "${job_status}" in
  failed)
    notify_failure
    ;;
  success)
    notify_success
    ;;
  *)
    log "job status is ${job_status}, skipping Feishu notification"
    ;;
esac
