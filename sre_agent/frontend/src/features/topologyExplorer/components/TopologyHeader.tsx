import { SectionHeader } from "../../../components/ui";
import { formatTimestamp } from "../../../utils/format";

type TopologyHeaderProps = {
  lastUpdated?: string;
};

function TopologyHeader({ lastUpdated }: TopologyHeaderProps) {
  return (
    <div className="page-intro topology-modified-header">
      <SectionHeader
        eyebrow="运行拓扑"
        title="运行拓扑"
        description="在关系图中查看资源、依赖、链路与影响范围，快速理解对象关系和影响半径。"
        actions={
          lastUpdated ? (
            <span className="topology-modified-header__updated">最近更新 {formatTimestamp(lastUpdated)}</span>
          ) : undefined
        }
      />
    </div>
  );
}

export default TopologyHeader;
