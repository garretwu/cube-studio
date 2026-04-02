import type { ReactNode } from "react";

import { cn } from "./cn";

type SurfaceCardProps = {
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  variant?: "panel" | "hero" | "soft";
};

function SurfaceCard({
  children,
  className,
  bodyClassName,
  title,
  description,
  actions,
  variant = "panel",
}: SurfaceCardProps) {
  return (
    <section className={cn("surface-card", `surface-card--${variant}`, className)}>
      {title || description || actions ? (
        <header className="surface-card__header">
          <div className="surface-card__heading">
            {title ? <h3 className="surface-card__title">{title}</h3> : null}
            {description ? <p className="surface-card__description">{description}</p> : null}
          </div>
          {actions ? <div>{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn("surface-card__body", bodyClassName)}>{children}</div>
    </section>
  );
}

export default SurfaceCard;
