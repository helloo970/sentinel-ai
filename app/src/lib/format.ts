export function formatTime(ts: number | null | undefined, locale = "en-US"): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(ts: number | null | undefined, locale = "en-US"): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString(locale, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatAgo(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "–";
  const diff = Math.max(0, Math.round(now - ts));
  if (diff < 60) return `${diff} s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return `${Math.round(diff / 86400)} d ago`;
}

export function formatEuro(value: number): string {
  return `${value.toFixed(value < 1 ? 3 : 2)} €`;
}

export function percentTone(value: number, warn = 75, crit = 90): "ok" | "warn" | "crit" {
  if (value >= crit) return "crit";
  if (value >= warn) return "warn";
  return "ok";
}

/** Seconds left in an undo window (never negative). */
export function remainingSeconds(expires: number | null | undefined, nowMs = Date.now()): number {
  if (!expires) return 0;
  return Math.max(0, Math.ceil(expires - nowMs / 1000));
}

export function severityTone(severity: string): "info" | "warn" | "crit" {
  if (severity === "critical") return "crit";
  if (severity === "warn") return "warn";
  return "info";
}
