import type { ReactNode } from "react";

interface PanelProps {
  title?: ReactNode;
  actions?: ReactNode;
  tone?: "warn" | "crit";
  className?: string;
  children: ReactNode;
}

export function Panel({ title, actions, tone, className, children }: PanelProps) {
  return (
    <section className={`panel ${className ?? ""}`} data-tone={tone}>
      <div className="panel-inner">
        {(title || actions) && (
          <header className="panel-head">
            {title ? <h2>{title}</h2> : <span />}
            {actions && <div className="row">{actions}</div>}
          </header>
        )}
        {children}
      </div>
    </section>
  );
}

export function Metric({
  label,
  value,
  sub,
  percent,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  percent?: number;
  tone?: "ok" | "warn" | "crit";
}) {
  return (
    <div className="metric stack" style={{ gap: 6 }}>
      <span className="label">{label}</span>
      <span className="value num">{value}</span>
      {percent !== undefined && (
        <div className="bar" data-tone={tone}>
          <span style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
        </div>
      )}
      {sub && (
        <span className="dim" style={{ fontSize: "var(--fs-sm)" }}>
          {sub}
        </span>
      )}
    </div>
  );
}

export function Chip({
  tone = "info",
  children,
  dot = false,
}: {
  tone?: "ok" | "warn" | "crit" | "info" | "muted";
  children: ReactNode;
  dot?: boolean;
}) {
  return (
    <span className={`chip ${tone}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}
