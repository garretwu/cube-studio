import { useState, type ReactNode } from "react";

import SidebarNav, { type SidebarNavSection } from "./SidebarNav";
import Topbar from "./Topbar";
import { cn } from "./cn";

type AppShellProps = {
  children: ReactNode;
  sections: SidebarNavSection[];
  helpLabel: string;
  siteLabel: string;
  userName: string;
  userMeta: string;
  adminLabel: string;
  title?: string;
  eyebrow?: string;
  subtitle?: string;
  brandSubtitle?: string;
  onBrandClick?: () => void;
};

function AppShell({
  children,
  sections,
  helpLabel,
  siteLabel,
  userName,
  userMeta,
  adminLabel,
  title,
  eyebrow,
  subtitle,
  brandSubtitle,
  onBrandClick,
}: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className={cn("shell-root", sidebarCollapsed && "shell-root--sidebar-collapsed")}>
      <div aria-hidden="true" className="shell-ambient">
        <img alt="" className="shell-ambient__orbit shell-ambient__orbit--primary" src="/brand-orbit.svg" />
        <img alt="" className="shell-ambient__orbit shell-ambient__orbit--secondary" src="/brand-orbit.svg" />
      </div>

      <aside className={cn("shell-sidebar", sidebarCollapsed && "shell-sidebar--collapsed")}>
        <SidebarNav
          brandSubtitle={brandSubtitle}
          collapsed={sidebarCollapsed}
          onBrandClick={onBrandClick}
          onItemSelect={() => setSidebarCollapsed(false)}
          onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
          sections={sections}
        />
      </aside>

      <div className="shell-main">
        <Topbar
          adminLabel={adminLabel}
          eyebrow={eyebrow}
          helpLabel={helpLabel}
          siteLabel={siteLabel}
          subtitle={subtitle}
          title={title}
          userMeta={userMeta}
          userName={userName}
        />
        <main className="shell-content">{children}</main>
      </div>
    </div>
  );
}

export default AppShell;
