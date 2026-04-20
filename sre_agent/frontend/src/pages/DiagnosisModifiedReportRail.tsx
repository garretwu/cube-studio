import type { ReactNode } from "react";

import type { DiagnosisModifiedReportView } from "./diagnosisModifiedReportModel";
import {
  DiagnosisSummarySection,
  DiagnosisContextSection,
  RemediationKeySection,
  ReportOverview,
  ReportSection,
} from "./DiagnosisModifiedReportSections";

function DiagnosisModifiedReportRail({
  actionContent,
  view,
}: {
  actionContent?: ReactNode;
  view: DiagnosisModifiedReportView;
}) {
  return (
    <aside aria-label="分析报告" className="diagnosis-modified-report-rail" data-testid="diagnosis-modified-report-rail">
      <div className="diagnosis-modified-report-rail__surface">
        <ReportOverview view={view.overview} />

        <div className="diagnosis-modified-report-rail__body">
          <ReportSection
            title="影响拓扑"
            titleHelp={"问题节点与受影响服务的关键证据范围。基于当前根因实体和受影响服务整理诊断上下文。"}
          >
            <DiagnosisContextSection context={view.context} />
          </ReportSection>

          <ReportSection title="归因分析">
            <DiagnosisSummarySection
              candidateChanges={view.candidateChanges}
              conclusion={view.conclusion}
              rootCause={view.rootCause}
            />
          </ReportSection>

          <ReportSection title="修复建议">
            <RemediationKeySection
              actionContent={actionContent}
              execution={view.execution}
              feedback={view.feedback}
              nextAction={view.nextAction}
              remediation={view.remediation}
            />
          </ReportSection>
        </div>
      </div>
    </aside>
  );
}

export default DiagnosisModifiedReportRail;
