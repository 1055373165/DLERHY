import s from "./StatusBadge.module.css";

type Tone = "active" | "success" | "warning" | "danger" | "muted";

export function StatusBadge({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className={`${s.badge} ${s[tone]}`}>
      <span className={s.dot} />
      {label}
    </span>
  );
}
