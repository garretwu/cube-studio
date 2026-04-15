import { Modal } from "antd";

import { AppButton, StatusChip } from "./ui";
import type { RemediationPlan } from "../api/types";
import { formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";

type ApprovalDialogProps = {
  open: boolean;
  plan?: RemediationPlan;
  onApprove: () => void;
  onReject: () => void;
  onCancel: () => void;
};

function ApprovalDialog({ open, plan, onApprove, onReject, onCancel }: ApprovalDialogProps) {
  return (
    <Modal
      className="approval-dialog-modal"
      destroyOnClose
      footer={
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 8 }}>
          <AppButton onClick={onReject} variant="danger">
            驳回
          </AppButton>
          <div style={{ display: "flex", gap: 12 }}>
            <AppButton onClick={onCancel} variant="secondary">
              稍后处理
            </AppButton>
            <AppButton onClick={onApprove} variant="primary">
              批准
            </AppButton>
          </div>
        </div>
      }
      onCancel={onCancel}
      open={open}
      title="批准修复方案"
    >
      <div className="page-stack">
        <div className="status-row">
          <StatusChip tone="accent">{plan?.priority ?? "P?"}</StatusChip>
          {plan?.confidence ? <StatusChip tone="info">{formatPercent(plan.confidence)}</StatusChip> : null}
          {plan?.safety_level ? <StatusChip tone="neutral">{formatWorkflowStatus(plan.safety_level)}</StatusChip> : null}
        </div>
        <div className="approval-dialog__summary">
          <p className="approval-dialog__summary-title">{plan?.description ?? "当前暂无可审批的修复方案。"}</p>
          <p className="approval-dialog__summary-copy">根因：{plan?.root_cause ?? "暂无"}</p>
          <p className="approval-dialog__summary-copy">预计影响：{plan?.estimated_impact ?? "暂无"}</p>
        </div>
      </div>
    </Modal>
  );
}

export default ApprovalDialog;
