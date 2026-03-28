import type { ReactNode } from "react";

import styles from "./Surface.module.css";

interface SurfaceProps {
  eyebrow: string;
  title: string;
  description?: string;
  aside?: ReactNode;
  children: ReactNode;
}

export function Surface({ eyebrow, title, description, aside, children }: SurfaceProps) {
  return (
    <section className={styles.surface}>
      <div className={styles.head}>
        <div className={styles.heading}>
          <div className={styles.eyebrow}>{eyebrow}</div>
          <h2 className={styles.title}>{title}</h2>
          {description ? <p className={styles.description}>{description}</p> : null}
        </div>
        {aside ? <div className={styles.aside}>{aside}</div> : null}
      </div>
      <div className={styles.body}>{children}</div>
    </section>
  );
}
