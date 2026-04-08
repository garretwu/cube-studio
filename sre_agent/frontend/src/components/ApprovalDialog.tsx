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
        <div className="mini-card">
          <p className="mini-card__title">{plan?.description ?? "当前暂无可审批的修复方案。"}</p>
          <p className="mini-card__copy">根因：{plan?.root_cause ?? "暂无"}</p>
          <p className="mini-card__copy">预计影响：{plan?.estimated_impact ?? "暂无"}</p>
        </div>
        {plan?.steps && plan.steps.length > 0 ? (
          <div className="mini-card" style={{ marginTop: 8 }}>
            <p className="mini-card__title">执行命令预览</p>
            <p className="mini-card__copy" style={{ marginBottom: 8 }}>
              以下为审批后将执行的详细命令，当前为模拟模式不会实际执行。
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
