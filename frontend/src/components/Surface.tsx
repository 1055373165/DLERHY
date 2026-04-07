import type { ReactNode } from "react";

import s from "./Surface.module.css";

interface SurfaceProps {
  eyebrow: string;
  title: string;
  description?: string;
  aside?: ReactNode;
  children: ReactNode;
}

export function Surface({ eyebrow, title, aside, children }: SurfaceProps) {
  return (
    <section className={s.surface}>
      <div className={s.head}>
        <span className={s.eyebrow}>{eyebrow}</span>
        <h2 className={s.title}>{title}</h2>
        {aside ? <div className={s.aside}>{aside}</div> : null}
      </div>
      <div className={s.body}>{children}</div>
    </section>
  );
}
