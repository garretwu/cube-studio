import { SectionHeader, StatusChip, SurfaceCard } from "../components/ui";

function HistoryPage() {
  return (
    <div className="page-grid history-page">
      <div className="page-intro">
        <SectionHeader
          title="历史频道"
          description="这里汇总最近的诊断会话入口与历史追溯说明，避免页面只保留一个空白壳。"
        />
        <div className="status-row">
          <StatusChip tone="neutral">历史会话入口</StatusChip>
          <StatusChip tone="info">从左侧栏选择一个会话</StatusChip>
        </div>
      </div>

      <SurfaceCard bodyClassName="page-stack" variant="hero">
        <p className="mini-card__title">当前页面是历史频道的占位视图</p>
        <p className="mini-card__copy">如果你要继续排查，请从左侧历史频道或诊断页进入具体会话。</p>
      </SurfaceCard>
    </div>
  );
}

export default HistoryPage;
