import { StatusChip } from "./ui";
import type { Observation, ThinkingStep } from "../api/types";
import { formatActionType, formatWorkflowStatus } from "../utils/display";
import { formatPercent, formatTimestamp } from "../utils/format";

type TimelineItem = ThinkingStep | Observation;

type ThinkingTimelineProps = {
  steps: TimelineItem[];
};

function isThought(item: TimelineItem): item is ThinkingStep {
  return "step" in item;
}

function ThinkingTimeline({ steps }: ThinkingTimelineProps) {
  return (
    <div className="timeline-list">
      {steps.map((item, index) =>
        isThought(item) ? (
          <article key={`${item.timestamp}-${index}`} className="timeline-card">
            <div className="status-row">
              <StatusChip tone="accent">步骤 {item.step}</StatusChip>
              <StatusChip tone="info">{formatActionType(item.action_type)}</StatusChip>
              {item.stage ? <StatusChip tone="warning">{formatWorkflowStatus(item.stage)}</StatusChip> : null}
              {item.confidence ? <StatusChip tone="neutral">{formatPercent(item.confidence)}</StatusChip> : null}
            </div>
            <p className="timeline-card__copy">{item.thought}</p>
            <p className="timeline-card__meta">
              {formatTimestamp(item.timestamp)}
              {item.tool_name ? ` | ${item.tool_name}` : ""}
              {item.tool_params ? ` | ${JSON.stringify(item.tool_params)}` : ""}
            </p>
          </article>
        ) : (
          <article key={`${item.timestamp}-${index}`} className="timeline-card">
            <div className="status-row">
              <StatusChip tone="success">观测结果</StatusChip>
              <StatusChip tone="neutral">{item.tool}</StatusChip>
            </div>
            <pre className="properties-block">{JSON.stringify(item.result, null, 2)}</pre>
            <p className="timeline-card__meta">{formatTimestamp(item.timestamp)}</p>
          </article>
        ),
      )}
    </div>
  );
}

export default ThinkingTimeline;
