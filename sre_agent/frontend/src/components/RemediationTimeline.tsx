import { useEffect, useMemo, useState } from "react";

import type { SessionEvent } from "../api/types";
import { formatTimestamp } from "../utils/format";
import { AppIcon, StatusChip } from "./ui";

type StepResult = {
  step_id?: number;
  tool?: string;
  command?: string;
  result?: unknown;
  success?: boolean;
  mocked?: boolean;
  message?: string;
};

type RemediationTimelineProps = {
  events: SessionEvent[];
  sessionId: string;
};

type TimelineField = {
  label: string;
  value: unknown;
  wide?: boolean;
};

const STATUS_LABELS: Record<string, string> = {
  approval_required: "等待审批",
  approval_rejected: "审批已拒",
  approved: "已批准",
  awaiting_approval: "等待审批",
  escalated: "已升级处理",
  execution_failed: "执行失败",
  execution_started: "开始修复",
  execution_succeeded: "执行成功",
  execution_timeout: "执行超时",
  failed: "失败",
  partially_resolved: "部分恢复",
  pending: "待开始",
  plan_revised: "方案已修订",
  proposed_fix_ready: "方案待执行",
  re_diagnosed: "复诊完成",
  rejected: "已拒绝",
  remediating: "修复中",
  resolved: "已恢复",
  rollback_failed: "回滚失败",
  rollback_started: "开始回滚",
  rollback_succeeded: "回滚成功",
  timeout: "超时",
  validating: "验证中",
};

const TERMINAL_STATUSES = new Set(["resolved", "failed", "escalated", "timeout", "rejected"]);
const ATTENTION_STATUSES = new Set(["failed", "escalated", "timeout", "rejected", "execution_failed", "rollback_failed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getEventStage(event: SessionEvent): string {
  if (event.type !== "remediation_progress") return event.type;
  const stage = String(event.data?.stage ?? "").trim();
  return stage || "remediation_progress";
}

function getStatusLabel(value?: string | null, fallback = "未知"): string {
  if (!value) return fallback;
  return STATUS_LABELS[value] ?? value;
}

function getStatusTone(value?: string | null): "neutral" | "accent" | "success" | "warning" | "danger" | "info" {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (ATTENTION_STATUSES.has(normalized)) return "danger";
  if (TERMINAL_STATUSES.has(normalized) || normalized === "execution_succeeded" || normalized === "rollback_succeeded") return "success";
  if (normalized === "remediating" || normalized === "execution_started" || normalized === "validating") return "warning";
  if (normalized === "approval_required" || normalized === "awaiting_approval") return "info";
  if (normalized === "approved" || normalized === "plan_revised") return "accent";
  return "neutral";
}

function getStepResults(event: SessionEvent): StepResult[] {
  const data = isRecord(event.data) ? event.data : {};
  const raw = data.step_results;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is StepResult => isRecord(item));
}

function getTimelineEventKey(event: SessionEvent): string {
  return `${event.type}-${event.timestamp}`;
}

function getTimelineEventTitle(event: SessionEvent): string {
  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) return data.message.trim();
  return getStatusLabel(getEventStage(event));
}

function formatResult(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatTimelineFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "无";
  if (typeof value === "string") return value.trim() || "无";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return formatResult(value);
}

function shouldSpanTimelineField(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) return true;
  return typeof value === "object" || (typeof value === "string" && value.length > 48);
}

export default function RemediationTimeline({ events, sessionId }: RemediationTimelineProps) {
  const [activeEventKey, setActiveEventKey] = useState<string | null>(null);

  useEffect(() => {
    setActiveEventKey(null);
  }, [sessionId]);

  const timelineEvents = useMemo(() => [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp)), [events]);

  if (timelineEvents.length === 0) {
    return (
      <div className="mini-card remediation-empty-state remediation-empty-state--compact remediation-record-table__embedded-card">
        <p className="mini-card__title">暂无执行事件</p>
        <p className="mini-card__copy">当前记录尚未进入执行阶段，或后端还没有返回可用事件流。</p>
      </div>
    );
  }

  return (
    <div className="remediation-timeline" role="list" aria-label="修复时间线">
      {timelineEvents.map((event, index) => {
        const stage = getEventStage(event);
        const data = isRecord(event.data) ? event.data : {};
        const stepResults = getStepResults(event);
        const eventKey = getTimelineEventKey(event);
        const title = getTimelineEventTitle(event);
        const isActive = activeEventKey === eventKey;
        const detailFields: TimelineField[] = [
          { label: "schema_version", value: event.schema_version },
          { label: "type", value: event.type },
          { label: "session_id", value: event.session_id },
          { label: "timestamp", value: event.timestamp },
          ...Object.entries(data)
            .filter(([key]) => key !== "step_results")
            .map(([key, value]) => ({ label: key, value, wide: shouldSpanTimelineField(value) })),
        ];

        return (
          <article key={`${event.type}-${event.timestamp}-${index}`} className={`remediation-timeline__item${isActive ? " remediation-timeline__item--active" : ""}`} role="listitem">
            <div className="remediation-timeline__rail" aria-hidden="true">
              <span className={`remediation-timeline__node remediation-timeline__node--${getStatusTone(stage)}`} />
            </div>
            <div className="remediation-timeline__body">
              <button
                type="button"
                className={`remediation-timeline__summary${isActive ? " remediation-timeline__summary--active" : ""}`}
                onClick={() => setActiveEventKey((current) => (current === eventKey ? null : eventKey))}
                aria-expanded={isActive}
                aria-label={`查看 ${title} 的详情`}
              >
                <div className="remediation-timeline__summary-main">
                  <div className="remediation-timeline__summary-head">
                    <p className="remediation-timeline__summary-title">{title}</p>
                    <p className="remediation-timeline__summary-time">{formatTimestamp(event.timestamp)}</p>
                  </div>
                  <div className="status-row remediation-timeline__summary-chips">
                    <StatusChip tone={getStatusTone(stage)}>{getStatusLabel(stage)}</StatusChip>
                    {typeof data.user === "string" && data.user.trim() ? <StatusChip tone="neutral">{data.user}</StatusChip> : null}
                    {typeof data.timeout_seconds === "number" ? <StatusChip tone="info">{`超时 ${data.timeout_seconds}s`}</StatusChip> : null}
                    {stepResults.length > 0 ? <StatusChip tone="neutral">{`步骤结果 ${stepResults.length}`}</StatusChip> : null}
                  </div>
                  {typeof data.message === "string" && data.message.trim() && data.message.trim() !== title ? (
                    <p className="remediation-timeline__summary-copy">{data.message}</p>
                  ) : null}
                </div>
                <span className="remediation-timeline__summary-action">
                  <AppIcon name={isActive ? "up" : "down"} size={14} />
                  <span>{isActive ? "收起详情" : "查看详情"}</span>
                </span>
              </button>

              {isActive ? (
                <div className="remediation-timeline__detail">
                  <div className="remediation-record-table__detail-form remediation-timeline__detail-form">
                    {detailFields.map((field, fieldIndex) => (
                      <div
                        key={`${eventKey}-${field.label}-${fieldIndex}`}
                        className={`remediation-record-table__detail-field${field.wide ? " remediation-record-table__detail-field--wide remediation-timeline__detail-field--wide" : ""}`}
                      >
                        <p className="remediation-record-table__detail-label">{field.label}</p>
                        <p className={`remediation-record-table__detail-value${field.wide ? " remediation-code-block" : ""}`}>{formatTimelineFieldValue(field.value)}</p>
                      </div>
                    ))}
                  </div>

                  {stepResults.length > 0 ? (
                    <div className="remediation-step-result-list remediation-timeline__result-list">
                      {stepResults.map((item, itemIndex) => (
                        <div className="remediation-step-result remediation-timeline__result-card" key={`${stage}-${item.step_id ?? itemIndex}`}>
                          <div className="status-row remediation-timeline__result-head">
                            <StatusChip tone={item.success === false ? "danger" : "success"}>{item.success === false ? "失败" : "成功"}</StatusChip>
                            <StatusChip tone="neutral">{`步骤 ${item.step_id ?? itemIndex + 1}`}</StatusChip>
                            {item.mocked ? <StatusChip tone="info">mock</StatusChip> : null}
                          </div>
                          <div className="remediation-timeline__result-fields">
                            <div className="remediation-record-table__detail-field">
                              <p className="remediation-record-table__detail-label">tool</p>
                              <p className="remediation-record-table__detail-value">{item.tool ?? "-"}</p>
                            </div>
                            <div className="remediation-record-table__detail-field">
                              <p className="remediation-record-table__detail-label">command</p>
                              <p className="remediation-record-table__detail-value remediation-code-block">{item.command ?? "-"}</p>
                            </div>
                            <div className="remediation-record-table__detail-field remediation-timeline__detail-field--wide">
                              <p className="remediation-record-table__detail-label">result</p>
                              <p className="remediation-record-table__detail-value remediation-code-block">{formatResult(item.result ?? item.message ?? "-")}</p>
                            </div>
                            <div className="remediation-record-table__detail-field">
                              <p className="remediation-record-table__detail-label">success</p>
                              <p className="remediation-record-table__detail-value">{typeof item.success === "boolean" ? String(item.success) : "-"}</p>
                            </div>
                            <div className="remediation-record-table__detail-field">
                              <p className="remediation-record-table__detail-label">mocked</p>
                              <p className="remediation-record-table__detail-value">{typeof item.mocked === "boolean" ? String(item.mocked) : "-"}</p>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}