import type { Status } from "../api/types";

export type CoreState =
  "idle" | "listening" | "thinking" | "speaking" | "alert" | "critical" | "halted";

/** Derives the hologram core state from the status and the voice state. */
export function deriveCoreState(
  status: Status | null,
  voiceState: string,
  busy = false,
): CoreState {
  if (status?.halted) return "halted";
  if (voiceState === "listening") return "listening";
  if (voiceState === "speaking") return "speaking";
  if (voiceState === "thinking" || busy) return "thinking";
  if (status && status.alerts.critical > 0) return "critical";
  if (status && status.alerts.warn > 0) return "alert";
  return "idle";
}

export type CoreMood = "calm" | "watch" | "alarm" | "halted";

/** Base mood of the core: calm, watchful (warnings/approvals) or alarm. */
export function deriveMood(status: Status | null): CoreMood {
  if (!status) return "calm";
  if (status.halted) return "halted";
  if (status.alerts.critical > 0) return "alarm";
  if (status.alerts.warn > 0 || status.pending_approvals > 0) return "watch";
  return "calm";
}
