import { Tabs } from "antd";

import type { TopologyObject, TopologyPath } from "../../../api/types";
import { AppButton, SurfaceCard } from "../../../components/ui";
import { formatImpactLevel, formatTopologyStatus, formatTopologyType } from "../formatters";
import type { DrawerTabKey } from "../types";

type BottomContextDrawerProps = {
  open: boolean;
  selectedNode?: TopologyObject;
  drawerTab: DrawerTabKey;
  onToggle: () => void;
  onTabChange: (key: DrawerTabKey) => void;
  paths: TopologyPath[];
  affectedObjects: TopologyObject[];
  neighbors: TopologyObject[];
  onSelectNode: (nodeId: string) => void;
};

function BottomContextDrawer({
  open,
  selectedNode,
  drawerTab,
  onToggle,
  onTabChange,
  paths,
  affectedObjects,
  neighbors,
  onSelectNode,
}: BottomContextDrawerProps) {
  return (
    <SurfaceCard
      className={`topology-modified-drawer ${open ? "topology-modified-drawer--open" : ""}`}
      description={open ? "查看当前选中对象的路径、受影响对象与邻居对象。" : undefined}
      title="上下文抽屉"
      actions={
        <AppButton size="sm" variant="secondary" onClick={onToggle}>
          {open ? "收起" : "展开"}
        </AppButton>
      }
    >
      {!open ? (
        <div className="topology-modified-drawer__dock">
          <span className="topology-modified-empty-copy">
            {selectedNode
              ? `已绑定对象 ${selectedNode.name}，展开后查看路径与邻居对象。`
              : "当前未选择对象，展开后查看关联路径、受影响对象与邻居对象。"}
          </span>
        </div>
      ) : (
        <Tabs
          activeKey={drawerTab}
          className="app-tabs"
          onChange={(key) => onTabChange(key as DrawerTabKey)}
          items={[
            {
              key: "paths",
              label: "关联路径",
              children: paths.length > 0 ? (
                <div className="topology-modified-drawer-list">
                  {paths.map((path) => (
                    <article key={path.id} className="topology-modified-drawer-card">
                      <p className="topology-modified-drawer-card__title">{path.summary}</p>
                      <p className="topology-modified-drawer-card__meta">
                        影响等级 {formatImpactLevel(path.impactLevel)}
                      </p>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="topology-modified-empty-copy">当前对象没有关联路径</p>
              ),
            },
            {
              key: "affected",
              label: "受影响对象",
              children: affectedObjects.length > 0 ? (
                <div className="topology-modified-link-list">
                  {affectedObjects.map((item) => (
                    <button
                      key={item.id}
                      className="topology-modified-link-list__item"
                      onClick={() => onSelectNode(item.id)}
                      type="button"
                    >
                      <span>{item.name}</span>
                      <span className="topology-modified-link-list__meta">
                        {formatTopologyType(item.type)} · {formatTopologyStatus(item.status)}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="topology-modified-empty-copy">暂无受影响对象</p>
              ),
            },
            {
              key: "neighbors",
              label: "邻居对象",
              children: neighbors.length > 0 ? (
                <div className="topology-modified-link-list">
                  {neighbors.map((item) => (
                    <button
                      key={item.id}
                      className="topology-modified-link-list__item"
                      onClick={() => onSelectNode(item.id)}
                      type="button"
                    >
                      <span>{item.name}</span>
                      <span className="topology-modified-link-list__meta">
                        {formatTopologyType(item.type)} · {formatTopologyStatus(item.status)}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="topology-modified-empty-copy">暂无邻居对象</p>
              ),
            },
          ]}
        />
      )}
    </SurfaceCard>
  );
}

export default BottomContextDrawer;
