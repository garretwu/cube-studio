import { useEffect } from "react";

import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useRemediationStore } from "../store/remediationStore";
import { formatVerificationMethod, formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";

function RemediationPage() {
  const { overview, approvalDialogOpen, fetchOverview, setApprovalDialogOpen, submitApproval } = useRemediationStore();

  useEffect(() => {
    void fetchOverview();
  }, [fetchOverview]);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="审阅建议修复路径、确认金丝雀策略与安全边界，只在置信度足够时再进入审批与执行。"
          eyebrow="执行控制"
          title="修复执行闸口"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="status-row">
            <StatusChip tone="warning">{formatWorkflowStatus(overview?.progress.status, "加载中")}</StatusChip>
            <StatusChip tone="accent">{overview?.plan.priority ?? "P?"}</StatusChip>
            <StatusChip tone="neutral">
              {overview?.plan.canary?.target_percentage
                ? `${overview.plan.canary.target_percentage * 100}% 金丝雀`
                : "无金丝雀"}
            </StatusChip>
            {overview?.plan.confidence ? <StatusChip tone="info">{formatPercent(overview.plan.confidence)}</StatusChip> : null}
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard
        actions={
          <AppButton disabled={!overview?.approval_required} onClick={() => setApprovalDialogOpen(true)} variant="primary">
            打开审批闸口
          </AppButton>
        }
        description="面向当前事件生成的执行动作、校验步骤与回滚抓手。"
        title="执行步骤"
      >
        <div className="mini-card-list">
          {(overview?.plan.steps ?? []).map((step) => (
            <div key={step.step_id} className="mini-card">
              <div className="status-row">
                <StatusChip tone="accent">步骤 {step.step_id}</StatusChip>
                <StatusChip tone="neutral">{step.tool}</StatusChip>
                <StatusChip tone="info">{formatVerificationMethod(step.verification.method)}</StatusChip>
              </div>
              <p className="mini-card__title">{step.description}</p>
              <p className="mini-card__copy">校验方式：{formatVerificationMethod(step.verification.method)}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard description="当前修复方案的分批放量与验证进度。" title="金丝雀进度">
        <CanaryProgress batches={overview?.progress.batch_status ?? []} />
      </SurfaceCard>

      <ApprovalDialog
        onApprove={() => void submitApproval(true)}
        onCancel={() => setApprovalDialogOpen(false)}
        onReject={() => void submitApproval(false)}
        open={approvalDialogOpen}
        plan={overview?.plan}
      />
    </div>
  );
}

export default RemediationPage;
