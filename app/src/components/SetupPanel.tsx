import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { Status } from "../api/types";
import { useStore } from "../state/store";
import { Chip, Panel } from "./Panel";

interface SetupState {
  home_city: string;
  google_account: string;
  quiet_hours: string;
  connected: Record<string, boolean>;
  profile: { saved: boolean; nodes: number; reminders: number };
}

const NONE: NonNullable<Status["warnings"]> = [];

// Never keys or passwords in the UI: that's what the terminal commands are for
const LOGIN_COMMANDS: Record<string, string> = {
  claude: "sentinel claude login",
  openrouter: "sentinel openrouter login",
  elevenlabs: "sentinel elevenlabs login",
  gmail: "sentinel gmail login",
  abusech: "sentinel abusech login",
  brain_backup: "sentinel brain password",
};

/** Warnings (what isn't working right now) and setup (home city, Google account, quiet hours, profile). */
export function SetupPanel() {
  const { t } = useTranslation();
  const warnings = useStore((s) => s.status?.warnings ?? NONE);
  const [state, setState] = useState<SetupState | null>(null);
  const [form, setForm] = useState({ home_city: "", google_account: "", quiet_hours: "" });
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .get<SetupState>("/setup")
      .then((s) => {
        setState(s);
        setForm({
          home_city: s.home_city,
          google_account: s.google_account,
          quiet_hours: s.quiet_hours,
        });
      })
      .catch(() => undefined);
  }, []);

  async function save() {
    try {
      setState(await api.put<SetupState>("/setup", form));
      setMessage(t("setup.saved"));
    } catch {
      setMessage(t("setup.invalid"));
    }
  }

  async function upload(file: File) {
    try {
      const data = JSON.parse(await file.text()) as unknown;
      const result = await api.post<{ nodes: number; reminders: number; rejected: number }>(
        "/brain/profile",
        data,
      );
      setMessage(t("setup.profileLoaded", result));
      setState(await api.get<SetupState>("/setup"));
    } catch (error) {
      setMessage(`${t("setup.profileError")}: ${String(error)}`);
    }
  }

  return (
    <Panel title={t("setup.title")}>
      {warnings.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <h3>{t("setup.warnings")}</h3>
          {warnings.map((w) => (
            <div key={w.key} className="setup-warning">
              <strong>{w.title}:</strong> {w.message}
            </div>
          ))}
        </div>
      )}
      <label className="stack" style={{ gap: 4 }}>
        <span className="label">{t("setup.city")}</span>
        <input
          className="input"
          value={form.home_city}
          placeholder="e.g. London"
          onChange={(e) => setForm({ ...form, home_city: e.target.value })}
        />
      </label>
      <label className="stack" style={{ gap: 4 }}>
        <span className="label">{t("setup.google")}</span>
        <input
          className="input"
          value={form.google_account}
          placeholder="name@gmail.com"
          onChange={(e) => setForm({ ...form, google_account: e.target.value })}
        />
      </label>
      <label className="stack" style={{ gap: 4 }}>
        <span className="label">{t("setup.quiet")}</span>
        <input
          className="input"
          value={form.quiet_hours}
          placeholder="23:00-07:00"
          onChange={(e) => setForm({ ...form, quiet_hours: e.target.value })}
        />
      </label>
      <div className="row">
        <button className="btn small primary" onClick={() => void save()}>
          {t("setup.save")}
        </button>
        {message && <span className="faint">{message}</span>}
      </div>

      <h3>{t("setup.profile")}</h3>
      <div className="row">
        <Chip tone={state?.profile.saved ? "ok" : "muted"} dot>
          {state?.profile.saved
            ? t("setup.profileSaved", state.profile)
            : t("setup.profileMissing")}
        </Chip>
        <button className="btn small" onClick={() => fileRef.current?.click()}>
          {t("setup.profileUpload")}
        </button>
        {state?.profile.saved && (
          <button
            className="btn small ghost"
            onClick={() =>
              void api
                .post<{ nodes: number; reminders: number; rejected: number }>(
                  "/brain/profile/reload",
                )
                .then((r) => setMessage(t("setup.profileLoaded", r)))
            }
          >
            {t("setup.profileReload")}
          </button>
        )}
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
      </div>

      <h3>{t("setup.connections")}</h3>
      <div className="list">
        {Object.entries(state?.connected ?? {}).map(([key, ok]) => (
          <div key={key} className="list-item">
            <span className={`stripe ${ok ? "" : "warn"}`} />
            <div>
              <strong>{t(`setup.conn.${key}`)}</strong>
              {!ok && LOGIN_COMMANDS[key] && (
                <div className="dim mono">
                  {t("setup.inTerminal")}: {LOGIN_COMMANDS[key]}
                </div>
              )}
            </div>
            <Chip tone={ok ? "ok" : "muted"}>{ok ? t("setup.ok") : t("setup.missing")}</Chip>
          </div>
        ))}
      </div>
    </Panel>
  );
}
