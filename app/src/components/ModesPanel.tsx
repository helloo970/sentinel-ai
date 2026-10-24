import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { runAction, useApi } from "../hooks/useApi";
import { formatAgo } from "../lib/format";
import { useStore } from "../state/store";
import { Chip, Panel } from "./Panel";

interface ModeInfo {
  name: string;
  title: string;
  launch: string[];
  close: string[];
  quiet: boolean;
  minutes: number | null;
}

interface ModeStatus {
  modes: ModeInfo[];
  active: { name: string; title: string; started: number; ends: number | null } | null;
  quiet: boolean;
  cleanup: { enabled: boolean; day: number; time: string; folders: string[] };
}

interface UndoEntry {
  id: string;
  created: number;
  expires: number;
  action: string;
  summary: string;
}

/** Gaming/focus mode with one click and the weekly cleanup. */
export function ModesPanel() {
  const { t } = useTranslation();
  const halted = useStore((s) => s.status?.halted);
  const status = useApi<ModeStatus>("/automation/modes", 5000);
  const undo = useApi<UndoEntry[]>("/undo", 30000);
  const data = status.data;
  const cleanupUndo = (undo.data ?? []).find((u) => u.action === "organize_folder");
  const days = t("modes.days", { returnObjects: true }) as string[];

  async function toggle(mode: ModeInfo) {
    if (data?.active?.name === mode.name) await runAction("stop_mode", {});
    else await runAction("start_mode", { name: mode.name });
    await status.reload();
  }

  async function revertCleanup(id: string) {
    await api.post(`/undo/${id}`);
    await undo.reload();
  }

  return (
    <Panel
      title={t("modes.title")}
      actions={data?.quiet ? <Chip tone="warn">{t("modes.quiet")}</Chip> : null}
    >
      <div className="grid g2">
        {(data?.modes ?? []).map((m) => {
          const active = data?.active?.name === m.name;
          return (
            <button
              key={m.name}
              className={`btn ${active ? "primary" : ""}`}
              disabled={halted && !active}
              onClick={() => void toggle(m)}
              style={{ flexDirection: "column", alignItems: "flex-start", gap: 4, padding: 12 }}
            >
              <span>
                {m.title} {active ? `· ${t("modes.on")}` : ""}
              </span>
              <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>
                {[
                  m.launch.length ? `+ ${m.launch.join(", ")}` : "",
                  m.close.length ? `− ${m.close.join(", ")}` : "",
                  m.minutes ? `${m.minutes} min` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {active && data?.active?.ends && (
                <span className="mono" style={{ textTransform: "none" }}>
                  {t("modes.until", {
                    time: new Date(data.active.ends * 1000).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    }),
                  })}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div className="list-item" style={{ marginTop: 8 }}>
        <span className={`stripe ${data?.cleanup.enabled ? "ok" : ""}`} />
        <div className="stack" style={{ gap: 2 }}>
          <span>{t("modes.cleanup")}</span>
          <span className="faint">
            {data?.cleanup.enabled
              ? t("modes.cleanupWhen", {
                  day: days[data.cleanup.day] ?? "",
                  time: data.cleanup.time,
                  folders: data.cleanup.folders.join(", ") || "–",
                })
              : t("modes.cleanupOff")}
          </span>
        </div>
        {cleanupUndo && (
          <button className="btn small" onClick={() => void revertCleanup(cleanupUndo.id)}>
            {t("modes.undoCleanup", { ago: formatAgo(cleanupUndo.created) })}
          </button>
        )}
      </div>
      <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
        {t("modes.hint")}
      </div>
    </Panel>
  );
}
