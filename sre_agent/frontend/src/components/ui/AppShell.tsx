import { useState, type ReactNode } from "react";

import SidebarNav, { type SidebarNavSection } from "./SidebarNav";
import Topbar from "./Topbar";
import { cn } from "./cn";

type AppShellProps = {
  children: ReactNode;
  sections: SidebarNavSection[];
  helpLabel: string;
  userName: string;
  userMeta: string;
  adminLabel: string;
  title?: string;
  eyebrow?: string;
  subtitle?: string;
  contentSpacing?: "default" | "compact";
  contentMode?: "default" | "workspace";
  contentWidthMode?: "default" | "wide" | "full";
  brandSubtitle?: string;
  onBrandClick?: () => void;
};

function AppShell({
  children,
  sections,
  helpLabel,
  userName,
  userMeta,
  adminLabel,
  title,
  eyebrow,
  subtitle,
  contentSpacing = "default",
  contentMode = "default",
  contentWidthMode = "default",
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

      {/* Workspace pages keep their own scroll container inside the shell. */}
      <div className={cn("shell-main", contentMode === "workspace" && "shell-main--workspace")}>
        <Topbar
          adminLabel={adminLabel}
          eyebrow={eyebrow}
          helpLabel={helpLabel}
          subtitle={subtitle}
          title={title}
          userMeta={userMeta}
          userName={userName}
        />
        <main
          className={cn(
            "shell-content",
            contentSpacing === "compact" && "shell-content--compact-page-chrome",
            contentMode === "workspace" && "shell-content--workspace-page",
            contentWidthMode === "wide" && "shell-content--wide",
            contentWidthMode === "full" && "shell-content--full-width",
          )}
        >
          {children}
        </main>
      </div>
    </div>
  );
}

export default AppShell;
