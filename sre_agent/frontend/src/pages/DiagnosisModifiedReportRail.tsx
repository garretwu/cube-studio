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
          <ReportSection title="诊断拓扑信息">
            <SessionLevelSection context={view.context} />
          </ReportSection>

          <ReportSection
            title="假设级信息"
            description="围绕候选假设持续更新证据、验证与置信度。"
          >
            <HypothesisLevelSection
              confidence={view.confidence}
              hypotheses={view.hypotheses}
              verification={view.verification}
            />
          </ReportSection>

          <ReportSection title="根因级信息" description="展示最终结论，并将修复方案挂在对应根因之下。">
            <RootCauseLevelSection
              candidateChanges={view.candidateChanges}
              conclusion={view.conclusion}
              execution={view.execution}
              feedback={view.feedback}
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
