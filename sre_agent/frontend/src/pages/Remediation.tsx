import { useEffect } from "react";
import { Button, Card, List, Space, Tag, Typography } from "antd";

import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { useRemediationStore } from "../store/remediationStore";

function RemediationPage() {
  const { overview, approvalDialogOpen, fetchOverview, setApprovalDialogOpen, submitApproval } = useRemediationStore();

  useEffect(() => {
    void fetchOverview();
  }, [fetchOverview]);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical">
          <Typography.Title level={2} style={{ margin: 0 }}>
            Remediation Gate
          </Typography.Title>
          <Space wrap>
            <Tag color="gold">{overview?.progress.status ?? "loading"}</Tag>
            <Tag color="cyan">{overview?.plan.priority ?? "P?"}</Tag>
            <Tag>{overview?.plan.canary?.target_percentage ? `${overview.plan.canary.target_percentage * 100}% canary` : "no canary"}</Tag>
          </Space>
        </Space>
      </Card>

      <Card
        className="panel-card"
        title="Plan Steps"
        extra={
          <Button type="primary" onClick={() => setApprovalDialogOpen(true)} disabled={!overview?.approval_required}>
            Open Approval Gate
          </Button>
        }
      >
        <List
          dataSource={overview?.plan.steps ?? []}
          renderItem={(step) => (
            <List.Item>
              <List.Item.Meta
                title={`${step.step_id}. ${step.description}`}
                description={`${step.tool} • verify via ${step.verification.method}`}
              />
            </List.Item>
          )}
        />
      </Card>

      <Card className="panel-card" title="Canary Progress">
        <CanaryProgress batches={overview?.progress.batch_status ?? []} />
      </Card>

      <ApprovalDialog
        open={approvalDialogOpen}
        plan={overview?.plan}
        onApprove={() => void submitApproval(true)}
        onReject={() => void submitApproval(false)}
        onCancel={() => setApprovalDialogOpen(false)}
      />
    </div>
  );
}

export default RemediationPage;
