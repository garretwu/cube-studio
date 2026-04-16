import { Modal } from "antd";

import type { RemediationPlan } from "../api/types";
import { formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";
import { AppButton, StatusChip } from "./ui";

type ApprovalDialogProps = {
  open: boolean;
  plan?: RemediationPlan;
  onApprove: () => void;
  onReject: () => void;
  onCancel: () => void;
  loadingAction?: "approve" | "reject" | null;
  errorMessage?: string | null;
  approveDisabled?: boolean;
  rejectDisabled?: boolean;
};

function ApprovalDialog({
  open,
  plan,
  onApprove,
  onReject,
  onCancel,
  loadingAction = null,
  errorMessage,
  approveDisabled = false,
  rejectDisabled = false,
}: ApprovalDialogProps) {
  const isBusy = loadingAction !== null;

  return (
    <Modal
      className="approval-dialog-modal"
      closable={!isBusy}
      destroyOnClose
      footer={
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 8 }}>
          <AppButton disabled={rejectDisabled || isBusy} loading={loadingAction === "reject"} onClick={onReject} variant="danger">
            驳回
          </AppButton>
          <div style={{ display: "flex", gap: 12 }}>
            <AppButton disabled={isBusy} onClick={onCancel} variant="secondary">
              稍后处理
            </AppButton>
            <AppButton disabled={approveDisabled || isBusy} loading={loadingAction === "approve"} onClick={onApprove} variant="primary">
              批准
            </AppButton>
          </div>
        </div>
      }
      mask={{ closable: !isBusy }}
      onCancel={() => {
        if (!isBusy) {
          onCancel();
        }
      }}
      open={open}
      title="批准修复方案"
    >
      <div className="page-stack">
        <div className="status-row">
          <StatusChip tone="accent">{plan?.priority ?? "P?"}</StatusChip>
          {plan?.confidence ? <StatusChip tone="info">{formatPercent(plan.confidence)}</StatusChip> : null}
          {plan?.safety_level ? <StatusChip tone="neutral">{formatWorkflowStatus(plan.safety_level)}</StatusChip> : null}
        </div>
        {errorMessage ? <p className="data-list__copy remediation-sidebar__error">{errorMessage}</p> : null}
        <div className="approval-dialog__summary">
          <p className="approval-dialog__summary-title">{plan?.description ?? "当前暂无可审批的修复方案。"}</p>
          <p className="approval-dialog__summary-copy">根因：{plan?.root_cause ?? "暂无"}</p>
          <p className="approval-dialog__summary-copy">预计影响：{plan?.estimated_impact ?? "暂无"}</p>
        </div>
        {plan?.steps && plan.steps.length > 0 ? (
          <div className="mini-card" style={{ marginTop: 8 }}>
            <p className="mini-card__title">执行步骤预览</p>
            <p className="mini-card__copy" style={{ marginBottom: 8 }}>
              以下内容会在审批通过后交由后端真实执行，页面会同步展示执行、观察和最终结果。
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {plan.steps.map((step) => (
                <div
                  key={step.step_id}
                  style={{
                    padding: "8px 12px",
                    background: "var(--color-surface-elevated, #f5f5f5)",
                    borderRadius: 6,
                    fontSize: 13,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <StatusChip tone="neutral">{`步骤 ${step.step_id}`}</StatusChip>
                    <span style={{ color: "var(--color-text-secondary, #666)" }}>{step.tool}</span>
                  </div>
                  <p style={{ margin: "0 0 4px", color: "var(--color-text-primary, #333)" }}>{step.description}</p>
                  <code
                    style={{
                      display: "block",
                      padding: "6px 10px",
                      background: "var(--color-surface-sunken, #1e1e1e)",
                      color: "var(--color-text-inverse, #e0e0e0)",
                      borderRadius: 4,
                      fontSize: 12,
                      fontFamily: "monospace",
                      wordBreak: "break-all",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {step.command ?? JSON.stringify(step.params)}
                  </code>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

export default ApprovalDialog;
