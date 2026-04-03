import { StatusChip } from "./ui";
import { formatWorkflowStatus } from "../utils/display";

type CanaryBatch = {
  batch: string;
  progress: number;
  status: string;
};

type CanaryProgressProps = {
  batches: CanaryBatch[];
};

function toneByStatus(status: string) {
  switch (status) {
    case "validating":
      return "warning";
    case "failed":
    case "timeout":
    case "escalated":
    case "rejected":
      return "danger";
    case "pending":
      return "neutral";
    default:
      return "success";
  }
}

function CanaryProgress({ batches }: CanaryProgressProps) {
  return (
    <div className="progress-list">
      {batches.map((item) => (
        <div key={item.batch} className="progress-list__item">
          <div className="progress-list__row">
            <strong>{item.batch}</strong>
            <StatusChip tone={toneByStatus(item.status)}>{formatWorkflowStatus(item.status)}</StatusChip>
          </div>
          <div className="progress-track">
            <div className="progress-track__fill" style={{ width: `${item.progress}%` }} />
          </div>
          <div className="progress-list__row">
            <span className="data-list__copy">发布完成度</span>
            <span>{item.progress}%</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default CanaryProgress;
