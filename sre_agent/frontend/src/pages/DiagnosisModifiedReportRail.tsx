import type { DiagnosisModifiedReportView } from "./diagnosisModifiedReportModel";
import {
  DEFAULT_REPORT_PREVIEW_MODULES,
  HypothesisLevelSection,
  ReportEmptyPreviewModule,
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
  const contextReady = view.context.state === "ready";
  const hypothesesReady = view.hypotheses.state === "ready";
  const rootCauseReady = view.rootCause.state === "ready";
  const allSectionsInPlaceholder = !contextReady && !hypothesesReady && !rootCauseReady;

  const contextModule = DEFAULT_REPORT_PREVIEW_MODULES.find((module) => module.key === "context");
  const hypothesesModule = DEFAULT_REPORT_PREVIEW_MODULES.find((module) => module.key === "hypotheses");
  const rootCauseModule = DEFAULT_REPORT_PREVIEW_MODULES.find((module) => module.key === "rootCause");

  return (
    <aside aria-label="分析报告" className="diagnosis-modified-report-rail" data-testid="diagnosis-modified-report-rail">
      <div className="diagnosis-modified-report-rail__surface">
        <ReportOverview view={view.overview} />

        <div
          className={[
            "diagnosis-modified-report-rail__body",
            allSectionsInPlaceholder ? "diagnosis-modified-report-rail__body--empty" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          data-testid="diagnosis-modified-report-body"
        >
          <div
            className="diagnosis-modified-report-rail__progressive-stack"
            data-testid="diagnosis-modified-report-progressive-stack"
          >
            <div
              className={[
                "diagnosis-modified-report-rail__progressive-item",
                contextReady
                  ? "diagnosis-modified-report-rail__progressive-item--revealed"
                  : "diagnosis-modified-report-rail__progressive-item--placeholder",
              ]
                .filter(Boolean)
                .join(" ")}
              data-state={contextReady ? "ready" : "placeholder"}
              data-testid="diagnosis-modified-report-progressive-context"
            >
              {contextReady ? (
                <ReportSection
                  title="诊断拓扑信息"
                  titleHelp="拓扑图优先基于会话返回的 topology_context 生成；若该上下文缺失，则回退使用诊断结果中的根因实体与受影响服务自动构建。"
                >
                  <SessionLevelSection context={view.context} />
                </ReportSection>
              ) : contextModule ? (
                <ReportEmptyPreviewModule
                  dataTestId="diagnosis-modified-report-placeholder-context"
                  description={contextModule.description}
                  index={0}
                  lines={contextModule.lines}
                  title={contextModule.title}
                />
              ) : null}
            </div>

            <div
              className={[
                "diagnosis-modified-report-rail__progressive-item",
                hypothesesReady
                  ? "diagnosis-modified-report-rail__progressive-item--revealed"
                  : "diagnosis-modified-report-rail__progressive-item--placeholder",
              ]
                .filter(Boolean)
                .join(" ")}
              data-state={hypothesesReady ? "ready" : "placeholder"}
              data-testid="diagnosis-modified-report-progressive-hypotheses"
            >
              {hypothesesReady ? (
                <ReportSection
                  title="候选假设验证"
                  titleHelp="每个候选假设单独承载验证依据与置信度变化，结论稳定后自动收起过程细节。"
                >
                  <HypothesisLevelSection hypotheses={view.hypotheses} />
                </ReportSection>
              ) : hypothesesModule ? (
                <ReportEmptyPreviewModule
                  dataTestId="diagnosis-modified-report-placeholder-hypotheses"
                  description={hypothesesModule.description}
                  index={1}
                  lines={hypothesesModule.lines}
                  title={hypothesesModule.title}
                />
              ) : null}
            </div>

            <div
              className={[
                "diagnosis-modified-report-rail__progressive-item",
                rootCauseReady
                  ? "diagnosis-modified-report-rail__progressive-item--revealed"
                  : "diagnosis-modified-report-rail__progressive-item--placeholder",
              ]
                .filter(Boolean)
                .join(" ")}
              data-state={rootCauseReady ? "ready" : "placeholder"}
              data-testid="diagnosis-modified-report-progressive-rootcause"
            >
              {rootCauseReady ? (
                <ReportSection
                  title="根因结论和修复方案"
                  titleHelp="展示最终结论，并将修复方案挂在对应根因之下。"
                >
                  <RootCauseLevelSection
                    candidateChanges={view.candidateChanges}
                    hypothesesState={view.hypotheses.state}
                    rootCause={view.rootCause}
                  />
                </ReportSection>
              ) : rootCauseModule ? (
                <ReportEmptyPreviewModule
                  dataTestId="diagnosis-modified-report-placeholder-rootcause"
                  description={rootCauseModule.description}
                  index={2}
                  lines={rootCauseModule.lines}
                  title={rootCauseModule.title}
                />
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}

export default DiagnosisModifiedReportRail;
