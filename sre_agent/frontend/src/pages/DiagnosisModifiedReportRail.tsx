import type { DiagnosisModifiedReportView } from "./diagnosisModifiedReportModel";
import {
  HypothesisLevelSection,
  ReportOverview,
  ReportSection,
  RootCauseLevelSection,
  SessionLevelSection,
} from "./DiagnosisModifiedReportSections";

function DiagnosisModifiedReportRail({
  view,
}: {
  view: DiagnosisModifiedReportView;
}) {
  return (
    <aside aria-label="分析报告" className="diagnosis-modified-report-rail" data-testid="diagnosis-modified-report-rail">
      <div className="diagnosis-modified-report-rail__surface">
        <ReportOverview view={view.overview} />

        <div className="diagnosis-modified-report-rail__body">
          <ReportSection
            title="诊断拓扑信息"
            description="拓扑图优先基于会话返回的 topology_context 生成；若该上下文缺失，则回退使用诊断结果中的根因实体与受影响服务自动构建。"
          >
            <SessionLevelSection context={view.context} />
          </ReportSection>

          <ReportSection
            title="候选假设验证"
            description="每个候选假设单独承载验证依据与置信度变化，结论稳定后自动收起过程细节。"
          >
            <HypothesisLevelSection hypotheses={view.hypotheses} />
          </ReportSection>

          <ReportSection
            title="根因级信息"
            description="展示最终结论，并将修复方案挂在对应根因之下。"
          >
            <RootCauseLevelSection
              candidateChanges={view.candidateChanges}
              conclusion={view.conclusion}
              hypothesesState={view.hypotheses.state}
              remediation={view.remediation}
              rootCause={view.rootCause}
            />
          </ReportSection>
        </div>
      </div>
    </aside>
  );
}

export default DiagnosisModifiedReportRail;
