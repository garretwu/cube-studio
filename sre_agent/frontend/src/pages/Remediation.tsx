import { useEffect } from "react";
<<<<<<< HEAD
import { Button, Card, List, Space, Tag, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import ApprovalDialog from "../components/ApprovalDialog";
=======

import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
import { useRemediationStore } from "../store/remediationStore";
import { formatVerificationMethod, formatWorkflowStatus } from "../utils/display";
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

  return (
    <div className="page-grid">
<<<<<<< HEAD
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
=======
      <div className="page-intro">
        <SectionHeader
          description="审阅建议修复路径、确认金丝雀策略与安全边界，只在置信度足够时再进入审批与执行。"
          eyebrow="执行控制"
          title="修复执行闸口"
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
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

<<<<<<< HEAD
      <Card className="panel-card" title="Winning Candidate">
        <Typography.Text>{loop?.winning_candidate?.root_cause ?? "none"}</Typography.Text>
      </Card>
=======
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
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)

      <ApprovalDialog
        onApprove={() => void submitApproval(true)}
        onCancel={() => setApprovalDialogOpen(false)}
        onReject={() => void submitApproval(false)}
        open={approvalDialogOpen}
<<<<<<< HEAD
        plan={undefined}
        onApprove={() => void submitApproval(true)}
        onReject={() => void submitApproval(false)}
        onCancel={() => setApprovalDialogOpen(false)}
=======
        plan={overview?.plan}
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
      />
    </div>
  );
}

export default RemediationPage;
