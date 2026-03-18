import { Modal, Space, Typography } from "antd";

import type { RemediationPlan } from "../api/types";

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
      title="Approve Remediation Plan"
      open={open}
      onOk={onApprove}
      okText="Approve"
      cancelText="Reject"
      onCancel={onCancel}
      footer={(_, { OkBtn, CancelBtn }) => (
        <Space>
          <CancelBtn />
          <Typography.Link onClick={onReject}>Reject</Typography.Link>
          <OkBtn />
        </Space>
      )}
    >
      <Space direction="vertical">
        <Typography.Text strong>{plan?.description}</Typography.Text>
        <Typography.Text type="secondary">Root cause: {plan?.root_cause ?? "n/a"}</Typography.Text>
        <Typography.Text type="secondary">Priority: {plan?.priority ?? "n/a"}</Typography.Text>
      </Space>
    </Modal>
  );
}

export default ApprovalDialog;
