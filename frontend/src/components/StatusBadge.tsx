import styles from "./StatusBadge.module.css";

type Tone = "active" | "success" | "warning" | "danger" | "muted";

export function StatusBadge({ label, tone }: { label: string; tone: Tone }) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{label}</span>;
}
