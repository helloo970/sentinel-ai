import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Program, Reminder, Routine } from "../api/types";
import { ModesPanel } from "../components/ModesPanel";
import { Panel } from "../components/Panel";
import { runAction, useApi } from "../hooks/useApi";
import { formatDateTime } from "../lib/format";
import { useStore } from "../state/store";

const CONTROLS: [string, string][] = [
  ["volume_down", "Quieter"],
  ["volume_up", "Louder"],
  ["mute", "Mute"],
  ["play_pause", "Play/Pause"],
  ["next_track", "Next"],
  ["lock", "Lock"],
];

export function Automation() {
  const { t } = useTranslation();
  const halted = useStore((s) => s.status?.halted);
  const programs = useApi<Program[]>("/automation/programs");
  const routines = useApi<Routine[]>("/automation/routines");
  const reminders = useApi<Reminder[]>("/automation/reminders", 10000);
  const settings = useApi<{ managed_folders: string[]; config_dir: string }>("/settings");
  const [text, setText] = useState("");
  const [minutes, setMinutes] = useState(10);

  async function addReminder() {
    if (!text.trim()) return;
    await runAction("set_reminder", { text: text.trim(), minutes });
    setText("");
    await reminders.reload();
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("automation.title")}</h1>
        <span className="faint mono">{settings.data?.config_dir}</span>
      </div>
      <ModesPanel />
      <div className="grid g2">
        <Panel title={t("automation.programs")}>
          <div className="list">
            {(programs.data ?? []).map((p) => (
              <div className="list-item" key={p.name}>
                <span className="stripe" />
                <div>
                  <div>{p.title}</div>
                  <div className="faint mono">{p.name}</div>
                </div>
                <div className="row">
                  <button
                    className="btn small"
                    disabled={halted}
                    onClick={() => void runAction("launch_program", { name: p.name })}
                  >
                    {t("common.start")}
                  </button>
                  <button
                    className="btn small ghost"
                    disabled={halted}
                    onClick={() => void runAction("close_program", { name: p.name })}
                  >
                    {t("common.close")}
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            Custom programs: programs.json in the config folder (name → absolute path).
          </div>
        </Panel>
        <Panel title={t("automation.routines")}>
          <div className="list">
            {(routines.data ?? []).map((r) => (
              <div className="list-item" key={r.name}>
                <span className="stripe" />
                <div>
                  <div>{r.title}</div>
                  <div className="faint mono">{r.steps.map((s) => s.action).join(" → ")}</div>
                </div>
                <button
                  className="btn small"
                  disabled={halted}
                  onClick={() => void runAction("run_routine", { name: r.name })}
                >
                  {t("common.run")}
                </button>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title={t("automation.reminders")}>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              void addReminder();
            }}
          >
            <input
              className="input grow"
              placeholder={t("automation.reminderText")}
              value={text}
              maxLength={200}
              onChange={(e) => setText(e.target.value)}
            />
            <input
              className="input"
              type="number"
              min={1}
              max={20160}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
              style={{ width: 90 }}
              aria-label={t("automation.minutes")}
            />
            <button className="btn primary" type="submit" disabled={halted}>
              {t("common.add")}
            </button>
          </form>
          <div className="list">
            {(reminders.data ?? []).length === 0 && (
              <div className="empty">{t("common.empty")}</div>
            )}
            {(reminders.data ?? []).map((r) => (
              <div className="list-item" key={r.id}>
                <span className="stripe" />
                <div>
                  <div>{r.text}</div>
                  <div className="faint mono">{formatDateTime(r.due)}</div>
                </div>
                <button
                  className="btn small ghost"
                  onClick={async () => {
                    await runAction("cancel_reminder", { id: r.id });
                    void reminders.reload();
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            ))}
          </div>
        </Panel>
        <div className="stack">
          <Panel title={t("automation.control")}>
            <div className="row">
              {CONTROLS.map(([command, label]) => (
                <button
                  key={command}
                  className="btn"
                  disabled={halted}
                  onClick={() => void runAction("system_control", { command })}
                >
                  {label}
                </button>
              ))}
            </div>
          </Panel>
          <Panel title={t("automation.files")}>
            <div className="list">
              {(settings.data?.managed_folders ?? []).map((folder) => (
                <div className="list-item" key={folder}>
                  <span className="stripe" />
                  <span className="mono">{folder}</span>
                  <button
                    className="btn small"
                    disabled={halted}
                    onClick={() => {
                      if (window.confirm(t("automation.confirmOrganize")))
                        void runAction("organize_folder", { folder });
                    }}
                  >
                    {t("automation.organize")}
                  </button>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
