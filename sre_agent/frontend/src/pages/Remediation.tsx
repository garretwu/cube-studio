import { useEffect } from "react";
import { Button, Card, List, Space, Tag, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import ApprovalDialog from "../components/ApprovalDialog";
import { useRemediationStore } from "../store/remediationStore";

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

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical">
          <Typography.Title level={2} style={{ margin: 0 }}>
            Remediation Gate
          </Typography.Title>
          <Space wrap>
            <Tag color="gold">{loop?.outcome ?? "loading"}</Tag>
            <Tag color="cyan">{`attempts ${loop?.attempts.length ?? 0}`}</Tag>
            <Tag>{`duration ${loop?.total_duration_seconds ?? 0}s`}</Tag>
          </Space>
        </Space>
      </Card>

      <Card
        className="panel-card"
        title="Loop Attempts"
        extra={
          <Button type="primary" onClick={() => setApprovalDialogOpen(true)} disabled={!loop?.session_id}>
            Open Approval Gate
          </Button>
        }
      >
        <List
          dataSource={loop?.attempts ?? []}
          renderItem={(attempt, index) => (
            <List.Item>
              <List.Item.Meta
                title={`${index + 1}. ${attempt.candidate.root_cause}`}
                description={`success=${attempt.remediation_result.success} steps=${attempt.remediation_result.steps_completed}/${attempt.remediation_result.steps_total} rolled_back=${attempt.rolled_back}`}
              />
            </List.Item>
          )}
        />
      </Card>

      <Card className="panel-card" title="Winning Candidate">
        <Typography.Text>{loop?.winning_candidate?.root_cause ?? "none"}</Typography.Text>
      </Card>

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
