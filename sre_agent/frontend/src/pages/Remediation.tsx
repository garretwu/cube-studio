import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useRemediationStore } from "../store/remediationStore";
import { formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";

function RemediationPage() {
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get("session_id") ?? "";
  const { loop, approvalDialogOpen, fetchLoop, setApprovalDialogOpen, submitApproval, setSessionId } = useRemediationStore();

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    setSessionId(sessionId);
    void fetchLoop(sessionId);
  }, [fetchLoop, sessionId, setSessionId]);

  const canaryBatches = useMemo(
    () =>
      (loop?.attempts ?? []).map((attempt, idx) => ({
        batch: `attempt-${idx + 1}`,
        progress: attempt.remediation_result.success ? 100 : 0,
        status: attempt.remediation_result.success ? "completed" : "failed",
      })),
    [loop?.attempts],
  );

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          eyebrow="执行控制"
          title="修复执行闸口"
          description="审阅建议修复路径、确认金丝雀策略与安全边界，只在置信度足够时再进入审批与执行。"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="status-row">
            <StatusChip tone="warning">{formatWorkflowStatus(loop?.outcome, "加载中")}</StatusChip>
            <StatusChip tone="neutral">尝试 {loop?.attempts.length ?? 0}</StatusChip>
            <StatusChip tone="accent">耗时 {loop?.total_duration_seconds ?? 0}s</StatusChip>
            {loop?.winning_candidate ? <StatusChip tone="info">{formatPercent(loop.winning_candidate.confidence)}</StatusChip> : null}
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard
        title="候选尝试"
        description="由 /api/sessions/{id}/loop 返回的循环尝试结果。"
        actions={
          <AppButton variant="primary" disabled={!loop?.session_id} onClick={() => setApprovalDialogOpen(true)}>
            打开审批闸口
          </AppButton>
        }
      >
        <div className="mini-card-list">
          {(loop?.attempts ?? []).map((attempt, idx) => (
            <div key={`${attempt.candidate.root_cause}-${idx}`} className="mini-card">
              <div className="status-row">
                <StatusChip tone="accent">#{idx + 1}</StatusChip>
                <StatusChip tone={attempt.remediation_result.success ? "success" : "danger"}>
                  {attempt.remediation_result.success ? "成功" : "失败"}
                </StatusChip>
                <StatusChip tone="neutral">{attempt.rolled_back ? "已回滚" : "未回滚"}</StatusChip>
              </div>
              <p className="mini-card__title">{attempt.candidate.root_cause}</p>
              <p className="mini-card__copy">
                步骤 {attempt.remediation_result.steps_completed}/{attempt.remediation_result.steps_total}
              </p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard title="金丝雀进度" description="基于尝试结果汇总的分批放量进度。">
        <CanaryProgress batches={canaryBatches} />
      </SurfaceCard>

      <ApprovalDialog
        open={approvalDialogOpen}
        plan={undefined}
        onApprove={() => void submitApproval(true)}
        onReject={() => void submitApproval(false)}
        onCancel={() => setApprovalDialogOpen(false)}
      />
    </div>
  );
}

export default RemediationPage;
