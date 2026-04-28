import { AppIcon, type AppIconName } from "./AppIcon";
import { cn } from "./cn";

export type SidebarNavItem = {
  key: string;
  label: string;
  icon?: AppIconName;
  active?: boolean;
  kind?: "default" | "history";
  status?: "diagnosing" | "completed";
  metaLabel?: string;
  metaTone?: "critical" | "warning" | "info" | "neutral";
  disabled?: boolean;
  onClick: () => void;
};

export type SidebarNavSection = {
  title: string;
  items: SidebarNavItem[];
  emptyLabel?: string;
  collapseBehavior?: "icon-only" | "hide";
};

type SidebarNavProps = {
  sections: SidebarNavSection[];
  brandSubtitle?: string;
  onBrandClick?: () => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onItemSelect?: () => void;
};

function SidebarNav({
  sections,
  brandSubtitle,
  onBrandClick,
  collapsed = false,
  onToggleCollapsed,
  onItemSelect,
}: SidebarNavProps) {
  const visibleSections = collapsed ? sections.filter((section) => section.collapseBehavior !== "hide") : sections;

  return (
    <>
      <div className={cn("shell-brand", collapsed && "shell-brand--collapsed")} aria-label="ChinClaw 品牌">
        <div className="shell-brand__identity">
          <button
            aria-label="打开设计令牌"
            className="shell-brand__trigger"
            onClick={onBrandClick}
            type="button"
          >
            <img alt="" className="shell-brand__image" src="/brand-orbit.svg" />
          </button>
          {!collapsed ? (
            <div className="shell-brand__text">
              <h1 className="shell-brand__title">ChinClaw</h1>
              {brandSubtitle ? <p className="shell-brand__subtitle">{brandSubtitle}</p> : null}
            </div>
          ) : null}
        </div>
        {onToggleCollapsed ? (
          <button
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
            className="shell-brand__collapse"
            onClick={onToggleCollapsed}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            type="button"
          >
            <AppIcon name={collapsed ? "right" : "left"} size={14} />
          </button>
        ) : null}
      </div>

      {visibleSections.map((section) => {
        const iconOnly = collapsed && section.collapseBehavior === "icon-only";

        return (
          <section key={section.title} className={cn("nav-section", iconOnly && "nav-section--icon-only")}>
            {!iconOnly ? <p className="nav-section__label">{section.title}</p> : null}
            <div className={cn("nav-list", iconOnly && "nav-list--icon-only")}>
              {section.items.map((item) => {
                const showIconOnly = iconOnly && Boolean(item.icon);

                return (
                  <button
                    key={item.key}
                    aria-label={showIconOnly ? item.label : undefined}
                    className={cn(
                      "nav-item",
                      showIconOnly && "nav-item--collapsed",
                      item.kind === "history" && "nav-item--history",
                      !item.icon && "nav-item--iconless",
                      item.active && "nav-item--active",
                    )}
                    disabled={item.disabled}
                    onClick={() => {
                      item.onClick();
                      if (collapsed) {
                        onItemSelect?.();
                      }
                    }}
                    title={showIconOnly ? item.label : undefined}
                    type="button"
                  >
                    {item.icon ? <AppIcon name={item.icon} size={16} /> : null}
                    {!showIconOnly ? (
                      <span className={cn("nav-item__content", item.kind === "history" && "nav-item__content--history")}>
                        <span className="nav-item__label">{item.label}</span>
                        {item.metaLabel || item.status ? (
                          <span className="nav-item__aside">
                            {item.metaLabel ? (
                              <span className={cn("nav-item__meta", item.metaTone && `nav-item__meta--${item.metaTone}`)}>
                                {item.metaLabel}
                              </span>
                            ) : null}
                            {item.status ? (
                              <span
                                aria-hidden="true"
                                className={cn("nav-item__status", `nav-item__status--${item.status}`)}
                                title={item.status === "diagnosing" ? "诊断中" : "已完成"}
                              >
                                <AppIcon name={item.status === "diagnosing" ? "timeCircle" : "checkmarkCircle"} size={12} />
                              </span>
                            ) : null}
                          </span>
                        ) : null}
                      </span>
                    ) : null}
                  </button>
                );
              })}
              {!iconOnly && !section.items.length && section.emptyLabel ? (
                <p className="nav-section__empty">{section.emptyLabel}</p>
              ) : null}
            </div>
          </section>
        );
      })}
    </>
  );
}

export default SidebarNav;
