import { AppIcon, type AppIconName } from "./AppIcon";
import { cn } from "./cn";

export type SidebarNavItem = {
  key: string;
  label: string;
  icon: AppIconName;
  active?: boolean;
  onClick: () => void;
};

export type SidebarNavSection = {
  title: string;
  items: SidebarNavItem[];
};

type SidebarNavProps = {
  sections: SidebarNavSection[];
  brandSubtitle?: string;
  onBrandClick?: () => void;
};

function SidebarNav({ sections, brandSubtitle, onBrandClick }: SidebarNavProps) {
  return (
    <>
      <div className="shell-brand" aria-label="Auto-SRE brand">
        <button
          aria-label="Open design tokens"
          className="shell-brand__trigger"
          onClick={onBrandClick}
          type="button"
        >
          <img alt="" className="shell-brand__image" src="/brand-orbit.svg" />
        </button>
        <div className="shell-brand__text">
          <h1 className="shell-brand__title">Auto-SRE</h1>
          {brandSubtitle ? <p className="shell-brand__subtitle">{brandSubtitle}</p> : null}
        </div>
      </div>

      {sections.map((section) => (
        <section key={section.title} className="nav-section">
          <p className="nav-section__label">{section.title}</p>
          <div className="nav-list">
            {section.items.map((item) => (
              <button
                key={item.key}
                className={cn("nav-item", item.active && "nav-item--active")}
                onClick={item.onClick}
                type="button"
              >
                <AppIcon name={item.icon} size={16} />
                <span className="nav-item__label">{item.label}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

export default SidebarNav;
