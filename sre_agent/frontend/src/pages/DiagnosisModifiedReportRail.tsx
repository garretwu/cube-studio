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
            description="仅展示告警主体与其直连关联实体；当缺少直连关系时，仅展示主体节点并给出提示。"
          >
            <SessionLevelSection context={view.context} />
          </ReportSection>

          <ReportSection
            title="候选假设验证"
            description={view.hypotheses.description}
          >
            <HypothesisLevelSection hypotheses={view.hypotheses} />
          </ReportSection>

          <ReportSection
            title="根因级信息"
            description="展示最终结论，并将修复方案挂在对应根因之下。"
          >
            <RootCauseLevelSection
              candidateChanges={view.candidateChanges}
              hypothesesState={view.hypotheses.state}
              rootCause={view.rootCause}
            />
          </ReportSection>
        </div>
      </div>
    </aside>
  );
}

export default DiagnosisModifiedReportRail;
