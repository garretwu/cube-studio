import type { ReactNode } from "react";

import AppButton from "./AppButton";
import StatusChip from "./StatusChip";

type TopbarProps = {
  helpLabel: string;
  userName: string;
  userMeta: string;
  adminLabel: ReactNode;
  title?: string;
  eyebrow?: string;
  subtitle?: string;
};

function Topbar({
  helpLabel,
  userName,
  userMeta,
  adminLabel,
  title,
  eyebrow,
  subtitle,
}: TopbarProps) {
  const hasContent = Boolean(title || eyebrow || subtitle);

  return (
    <header className={hasContent ? "topbar" : "topbar topbar--actions-only"}>
      {hasContent ? (
        <div className="topbar__content">
          <div className="topbar__heading">
            {eyebrow ? <p className="topbar__eyebrow">{eyebrow}</p> : null}
            {title ? <h2 className="topbar__title">{title}</h2> : null}
            {subtitle ? <p className="topbar__copy">{subtitle}</p> : null}
          </div>
          <div className="topbar__inline-slot" id="topbar-inline-slot" />
        </div>
      ) : null}

      <div className="topbar__actions">
        <AppButton iconLeft="help" size="sm" variant="secondary">
          {helpLabel}
        </AppButton>
        <div className="topbar-user">
          <div className="topbar-user__avatar">SZ</div>
          <div>
            <p className="topbar-user__name">{userName}</p>
            <p className="topbar-user__meta">{userMeta}</p>
          </div>
          <StatusChip tone="neutral">{adminLabel}</StatusChip>
        </div>
      </div>
    </header>
  );
}

export default Topbar;

