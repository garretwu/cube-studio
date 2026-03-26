import type { ReactNode } from "react";

import SidebarNav, { type SidebarNavSection } from "./SidebarNav";
import Topbar from "./Topbar";

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
  return (
    <div className="shell-root">
      <div aria-hidden="true" className="shell-ambient">
        <img alt="" className="shell-ambient__orbit shell-ambient__orbit--primary" src="/brand-orbit.svg" />
        <img alt="" className="shell-ambient__orbit shell-ambient__orbit--secondary" src="/brand-orbit.svg" />
      </div>

      <aside className="shell-sidebar">
        <SidebarNav brandSubtitle={brandSubtitle} onBrandClick={onBrandClick} sections={sections} />
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
