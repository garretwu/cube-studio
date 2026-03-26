import type { ReactNode } from "react";

import { cn } from "./cn";

type SectionHeaderProps = {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  align?: "start" | "center";
  size?: "page" | "section";
  className?: string;
};

function SectionHeader({
  eyebrow,
  title,
  description,
  actions,
  align = "start",
  size = "page",
  className,
}: SectionHeaderProps) {
  return (
    <div
      className={cn(
        "section-header",
        `section-header--${size}`,
        align === "center" && "section-header--center",
        Boolean(actions) && "section-header--with-actions",
        className,
      )}
    >
      <div className="section-header__main">
        {eyebrow ? <p className="section-header__eyebrow">{eyebrow}</p> : null}
        <h2 className="section-header__title">{title}</h2>
        {description ? <p className="section-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="section-header__actions">{actions}</div> : null}
    </div>
  );
}

export default SectionHeader;
