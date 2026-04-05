import type { ReactNode } from "react";

import s from "./Surface.module.css";

interface SurfaceProps {
  eyebrow: string;
  title: string;
  description?: string;
  aside?: ReactNode;
  children: ReactNode;
}

export function Surface({ eyebrow, title, description, aside, children }: SurfaceProps) {
  return (
    <section className={s.surface}>
      <div className={s.head}>
        <div className={s.titleBar}>
          <span className={s.titleDot} />
          <span className={s.eyebrow}>{eyebrow}</span>
        </div>
        <div className={s.headContent}>
          <div className={s.heading}>
            <h2 className={s.title}>{title}</h2>
            {description ? <p className={s.description}>{description}</p> : null}
          </div>
          {aside ? <div className={s.aside}>{aside}</div> : null}
        </div>
      </div>
      <div className={s.body}>{children}</div>
    </section>
  );
}
