import { StatusChip } from "./ui";

type CanaryBatch = {
  batch: string;
  progress: number;
  status: string;
};

type CanaryProgressProps = {
  batches: CanaryBatch[];
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待开始",
  validating: "验证中",
  resolved: "已完成",
  success: "已完成",
  failed: "执行失败",
  timeout: "执行超时",
  escalated: "已升级处理",
  rejected: "已驳回",
};

function toneByStatus(status: string) {
  switch (status) {
    case "validating":
      return "warning" as const;
    case "pending":
      return "neutral" as const;
    case "failed":
    case "timeout":
    case "escalated":
    case "rejected":
      return "danger" as const;
    case "resolved":
    case "success":
      return "success" as const;
    default:
      return "info" as const;
  }
}

function getStatusLabel(status: string) {
  return STATUS_LABELS[status] ?? status;
}

function CanaryProgress({ batches }: CanaryProgressProps) {
  if (batches.length === 0) {
    return (
      <div className="mini-card remediation-empty-state remediation-empty-state--compact">
        <p className="mini-card__title">暂无批次进度</p>
        <p className="mini-card__copy">当前接口还没有返回细粒度批次信息，可以先结合金丝雀策略和时间线判断执行阶段。</p>
      </div>
    );
  }

  return (
    <div className="progress-list">
      {batches.map((item) => (
        <div key={item.batch} className="progress-list__item">
          <div className="progress-list__row">
            <strong>{item.batch}</strong>
            <StatusChip tone={toneByStatus(item.status)}>{getStatusLabel(item.status)}</StatusChip>
          </div>
          <div className="progress-track remediation-progress-track remediation-progress-track--canary">
            <div className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--canary" style={{ width: `${item.progress}%` }} />
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
