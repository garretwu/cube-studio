import { useEffect, useMemo, useState } from "react";

import type { SessionEvent } from "../api/types";
import { formatTimestamp } from "../utils/format";
import {
  formatRemediationEventMessage,
  getRemediationEventStage,
  getRemediationStepDisplayIndex,
} from "../utils/remediationEventText";
import { AppIcon, StatusChip } from "./ui";

type StepResult = {
  step_id?: number;
  display_step_index?: number;
  step_index?: number;
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
  autoPlay?: boolean;
  autoPlayIntervalMs?: number;
  onPlaybackChange?: (snapshot: TimelinePlaybackSnapshot) => void;
};

type TimelineField = {
  label: string;
  value: unknown;
  wide?: boolean;
};

type TimelineNodeState = "running" | "done" | "error" | "pending";

type TimelinePlaybackSnapshot = {
  activeIndex: number | null;
  completedCount: number;
  totalCount: number;
  visibleCount: number;
  overallPercent: number;
};

const ATTENTION_STATUSES = new Set(["failed", "escalated", "timeout", "rejected", "execution_failed", "rollback_failed"]);
const RUNNING_STATUSES = new Set(["remediating", "execution_started", "validating", "rollback_started"]);
const DEFAULT_AUTOPLAY_INTERVAL_MS = 2200;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getEventStage(event: SessionEvent): string {
  return getRemediationEventStage(event);
}

function getStepResults(event: SessionEvent): StepResult[] {
  const data = isRecord(event.data) ? event.data : {};
  const raw = data.step_results;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is StepResult => isRecord(item));
}

function getTimelineEventKey(event: SessionEvent, index: number): string {
  return `${event.type}-${event.timestamp}-${index}`;
}

function getTimelineEventTitle(event: SessionEvent): string {
  return formatRemediationEventMessage(event);
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

const HIDDEN_DETAIL_KEYS = new Set(["message", "stage", "step_results", "display_step_index", "step_index"]);

function getTimelineNodeState(stage: string, isLatestEvent: boolean): TimelineNodeState {
  const normalized = stage.trim().toLowerCase();
  if (ATTENTION_STATUSES.has(normalized)) return "error";
  if (isLatestEvent && RUNNING_STATUSES.has(normalized)) return "running";
  return "done";
}

export default function RemediationTimeline({
  autoPlay = false,
  autoPlayIntervalMs = DEFAULT_AUTOPLAY_INTERVAL_MS,
  events,
  onPlaybackChange,
  sessionId,
}: RemediationTimelineProps) {
  const [manualActiveEventKey, setManualActiveEventKey] = useState<string | null>(null);
  const [activePlaybackIndex, setActivePlaybackIndex] = useState<number | null>(autoPlay ? 0 : null);

  const timelineEvents = useMemo(() => [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp)), [events]);
  const visibleTimelineEvents = useMemo(() => {
    if (!autoPlay || timelineEvents.length === 0) return timelineEvents;
    if (activePlaybackIndex === null) return timelineEvents;
    return timelineEvents.slice(0, Math.min(activePlaybackIndex + 1, timelineEvents.length));
  }, [activePlaybackIndex, autoPlay, timelineEvents]);

  useEffect(() => {
    setManualActiveEventKey(null);
    if (autoPlay && timelineEvents.length > 0) {
      setActivePlaybackIndex(0);
      return;
    }
    setActivePlaybackIndex(null);
  }, [autoPlay, sessionId, timelineEvents.length]);

  useEffect(() => {
    if (!autoPlay || timelineEvents.length === 0 || activePlaybackIndex === null) return;
    const lastIndex = timelineEvents.length - 1;
    const timerId = window.setTimeout(() => {
      setActivePlaybackIndex((current) => {
        if (current === null) return null;
        if (current >= lastIndex) return null;
        return current + 1;
      });
    }, Math.max(1000, autoPlayIntervalMs));
    return () => window.clearTimeout(timerId);
  }, [activePlaybackIndex, autoPlay, autoPlayIntervalMs, timelineEvents.length]);

  useEffect(() => {
    if (!autoPlay || manualActiveEventKey === null || activePlaybackIndex === null) return;
    const autoEventKey = getTimelineEventKey(timelineEvents[activePlaybackIndex], activePlaybackIndex);
    if (manualActiveEventKey !== autoEventKey) {
      setManualActiveEventKey(null);
    }
  }, [autoPlay, activePlaybackIndex, manualActiveEventKey, timelineEvents]);

  useEffect(() => {
    if (!onPlaybackChange) return;
    const totalCount = timelineEvents.length;
    if (totalCount === 0) {
      onPlaybackChange({
        activeIndex: null,
        completedCount: 0,
        overallPercent: 0,
        totalCount: 0,
        visibleCount: 0,
      });
      return;
    }

    if (!autoPlay) {
      onPlaybackChange({
        activeIndex: null,
        completedCount: totalCount,
        overallPercent: 100,
        totalCount,
        visibleCount: totalCount,
      });
      return;
    }

    if (activePlaybackIndex === null) {
      onPlaybackChange({
        activeIndex: null,
        completedCount: totalCount,
        overallPercent: 100,
        totalCount,
        visibleCount: totalCount,
      });
      return;
    }

    onPlaybackChange({
      activeIndex: activePlaybackIndex,
      completedCount: Math.max(0, Math.min(activePlaybackIndex, totalCount)),
      overallPercent: Math.max(0, Math.min(100, Math.round((activePlaybackIndex / Math.max(totalCount, 1)) * 100))),
      totalCount,
      visibleCount: Math.max(0, Math.min(activePlaybackIndex + 1, totalCount)),
    });
  }, [activePlaybackIndex, autoPlay, onPlaybackChange, timelineEvents.length]);

  if (visibleTimelineEvents.length === 0) {
    return (
      <div className="mini-card remediation-empty-state remediation-empty-state--compact remediation-record-table__embedded-card">
        <p className="mini-card__title">暂无执行事件</p>
        <p className="mini-card__copy">当前记录尚未进入执行阶段，或后端还没有返回可用事件流。</p>
      </div>
    );
  }

  const autoActiveEventKey =
    autoPlay && activePlaybackIndex !== null ? getTimelineEventKey(timelineEvents[activePlaybackIndex], activePlaybackIndex) : null;
  const effectiveActiveEventKey = autoPlay ? (manualActiveEventKey ?? autoActiveEventKey) : manualActiveEventKey;

  return (
    <div className="remediation-timeline" role="list" aria-label="修复时间线">
      {visibleTimelineEvents.map((event, index) => {
        const stage = getEventStage(event);
        const data = isRecord(event.data) ? event.data : {};
        const stepResults = getStepResults(event);
        const eventKey = getTimelineEventKey(event, index);
        const title = getTimelineEventTitle(event);
        const isActive = effectiveActiveEventKey === eventKey;
        const isLatestEvent = index === timelineEvents.length - 1;
        const nodeState: TimelineNodeState = autoPlay
          ? (() => {
              const normalized = stage.trim().toLowerCase();
              const isAttention = ATTENTION_STATUSES.has(normalized);
              if (activePlaybackIndex === null) return isAttention ? "error" : "done";
              if (index < activePlaybackIndex) return isAttention ? "error" : "done";
              if (index === activePlaybackIndex) {
                if (isAttention) return "error";
                return RUNNING_STATUSES.has(normalized) ? "running" : "done";
              }
              return "pending";
            })()
          : getTimelineNodeState(stage, isLatestEvent);
        const nodeIconName =
          nodeState === "running" ? "refresh" : nodeState === "error" ? "cancelCircle" : nodeState === "pending" ? "infoCircle" : "checkmarkCircle";
        const isRunningLog = nodeState === "running";
        const logStateLabel = isRunningLog ? "执行中" : nodeState === "error" ? "执行异常" : null;
        const detailFields: TimelineField[] = [
          ...Object.entries(data)
            .filter(([key]) => !HIDDEN_DETAIL_KEYS.has(key))
            .map(([key, value]) => ({ label: key, value, wide: shouldSpanTimelineField(value) })),
        ];

        return (
          <article key={eventKey} className={`remediation-timeline__item${isActive ? " remediation-timeline__item--active" : ""}`} role="listitem">
            <div className="remediation-timeline__rail" aria-hidden="true">
              <span className={`remediation-timeline__node remediation-timeline__node--${nodeState}`}>
                <AppIcon
                  name={nodeIconName}
                  size={12}
                  className={`remediation-timeline__node-icon${nodeState === "running" ? " remediation-timeline__node-icon--spinning" : ""}`}
                />
              </span>
            </div>
            <div className="remediation-timeline__body">
              <button
                type="button"
                className={`remediation-timeline__summary${isActive ? " remediation-timeline__summary--active" : ""}`}
                onClick={() => setManualActiveEventKey((current) => (current === eventKey ? null : eventKey))}
                aria-expanded={isActive}
                aria-label={`查看 ${title} 的详情`}
              >
                <div className="remediation-timeline__summary-main">
                  <div className="remediation-timeline__summary-head">
                    <p className="remediation-timeline__summary-title">{title}</p>
                    <p className="remediation-timeline__summary-time">{formatTimestamp(event.timestamp)}</p>
                  </div>
                  {logStateLabel ? <p className={`remediation-timeline__state-chip remediation-timeline__state-chip--${nodeState}`}>{logStateLabel}</p> : null}
                </div>
                <span className="remediation-timeline__summary-action">
                  <AppIcon name={isActive ? "up" : "down"} size={13} />
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
                      {stepResults.map((item, itemIndex) => {
                        const stepDisplayIndex = getRemediationStepDisplayIndex(item, itemIndex + 1);
                        return (
                          <div className="remediation-step-result remediation-timeline__result-card" key={`${stage}-${item.step_id ?? itemIndex}`}>
                            <div className="status-row remediation-timeline__result-head">
                              <StatusChip tone={item.success === false ? "danger" : "success"}>{item.success === false ? "失败" : "成功"}</StatusChip>
                              <StatusChip tone="neutral">{`步骤 ${stepDisplayIndex}`}</StatusChip>
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
                            </div>
                          </div>
                        );
                      })}
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
