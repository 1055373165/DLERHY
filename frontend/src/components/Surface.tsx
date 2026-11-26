import type { ReactNode } from "react";

import s from "./Surface.module.css";

interface SurfaceProps {
  eyebrow?: string;
  title: string;
  description?: string;
  aside?: ReactNode;
  /** 1 when this section is the whole page: every page needs exactly one h1. */
  level?: 1 | 2;
  children: ReactNode;
}

export function Surface({ eyebrow, title, aside, level = 2, children }: SurfaceProps) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <section className={s.surface}>
      <div className={s.head}>
        <div>
          {eyebrow && <span className={s.eyebrow}>{eyebrow}</span>}
          <Heading className={s.title}>{title}</Heading>
        </div>
        {aside ? <div className={s.aside}>{aside}</div> : null}
      </div>
      <div className={s.body}>{children}</div>
    </section>
  );
}
