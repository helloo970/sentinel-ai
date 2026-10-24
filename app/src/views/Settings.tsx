import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { Plugin } from "../api/types";
import { AutonomyControl } from "../components/AutonomyControl";
import { Chip, Panel } from "../components/Panel";
import { MicSettings } from "../components/MicSettings";
import { PhonePanel } from "../components/PhonePanel";
import { ResponsePanel } from "../components/ResponsePanel";
import { SetupPanel } from "../components/SetupPanel";
import { useApi } from "../hooks/useApi";
import { formatEuro } from "../lib/format";
import { loadPrefs, type Prefs, savePrefs } from "../lib/prefs";
import { playSiren } from "../lib/siren";
import { pushToast, store, useStore } from "../state/store";

type SettingsData = Record<string, string | number | boolean | string[] | null>;

export function Settings() {
  const { t } = useTranslation();
  const status = useStore((s) => s.status);
  const settings = useApi<SettingsData>("/settings");
  const plugins = useApi<Plugin[]>("/plugins");
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  function update(next: Prefs) {
    setPrefs(next);
    savePrefs(next);
    store.set({ layout: next.layout, cameraEnabled: next.camera });
  }

  async function togglePlugin(plugin: Plugin) {
    await api.put(`/plugins/${plugin.name}`, { enabled: !plugin.enabled });
    await plugins.reload();
  }

  async function testNotify() {
    const result = await api.post<{ ok: boolean; error?: string }>("/notify/test");
    pushToast({
      title: "ntfy",
      message: result.ok ? "Push sent" : (result.error ?? "Error"),
      severity: result.ok ? "info" : "warn",
    });
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("settings.title")}</h1>
        <span className="faint mono">v{status?.version}</span>
      </div>
      <div className="grid g2">
        <SetupPanel />
        <Panel title={t("autonomy.label")}>
          <AutonomyControl />
          <div className="list">
            {[0, 1, 2, 3].map((n) => (
              <div key={n} className="list-item">
                <span className={`stripe ${n === 3 ? "warn" : ""}`} />
                <div>
                  <strong>
                    {n} · {t(`autonomy.${n}`)}
                  </strong>
                  <div className="dim">{t(`autonomy.desc${n}`)}</div>
                </div>
                {status?.autonomy === n ? <Chip tone="ok">active</Chip> : <span />}
              </div>
            ))}
          </div>
          <p className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            Irreversible actions (deleting, system settings) always need your confirmation. More
            than {status?.autonomous_limit ?? 10} autonomous actions per hour automatically lower
            the level to 1.
          </p>
        </Panel>
        <Panel title={t("settings.interface")}>
          <label className="row">
            <input
              type="checkbox"
              checked={prefs.motion}
              onChange={(e) => update({ ...prefs, motion: e.target.checked })}
            />
            {t("settings.motion")}
          </label>
          <label className="row">
            <input
              type="checkbox"
              checked={prefs.alarmSound}
              onChange={(e) => update({ ...prefs, alarmSound: e.target.checked })}
            />
            {t("settings.alarmSound")}
            <button type="button" className="btn small ghost" onClick={() => playSiren("critical")}>
              {t("settings.alarmTry")}
            </button>
          </label>
          <label className="row">
            <input
              type="checkbox"
              checked={prefs.camera}
              onChange={(e) => update({ ...prefs, camera: e.target.checked })}
            />
            {t("settings.camera")}
          </label>
          <label className="row">
            {t("settings.layout")}
            <select
              className="select"
              style={{ width: "auto" }}
              value={prefs.layout}
              onChange={(e) => update({ ...prefs, layout: e.target.value as Prefs["layout"] })}
            >
              <option value="voice">{t("settings.layoutVoice")}</option>
              <option value="menu">{t("settings.layoutMenu")}</option>
            </select>
          </label>
          <h3>{t("settings.costs")}</h3>
          <div className="dim">
            Today {formatEuro(status?.costs.today_eur ?? 0)} /{" "}
            {formatEuro(status?.costs.daily_limit_eur ?? 0)} · Month{" "}
            {formatEuro(status?.costs.month_eur ?? 0)} /{" "}
            {formatEuro(status?.costs.monthly_limit_eur ?? 0)}
          </div>
        </Panel>
        <MicSettings />
        <Panel
          title={t("settings.config")}
          actions={
            <button className="btn small" onClick={() => void testNotify()}>
              {t("settings.notifyTest")}
            </button>
          }
        >
          <p className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            {t("settings.configHint")}
          </p>
          <table>
            <tbody>
              {Object.entries(settings.data ?? {}).map(([key, value]) => (
                <tr key={key}>
                  <td className="mono dim">{key}</td>
                  <td className="mono">
                    {Array.isArray(value) ? value.join(", ") || "–" : String(value ?? "–")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title={t("settings.plugins")}>
          <p className="faint" style={{ fontSize: "var(--fs-sm)" }}>
            Plugins contain data only (routines, news feeds) and are off by default.
          </p>
          <div className="list">
            {(plugins.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
            {(plugins.data ?? []).map((p) => (
              <div className="list-item" key={p.name}>
                <span className="stripe" />
                <div>
                  <strong>{p.name}</strong> <span className="faint">v{p.version}</span>
                  <div className="dim">{p.error ?? p.description}</div>
                  <div className="faint mono">
                    {p.routines.length} routines · {p.feeds} feeds
                  </div>
                </div>
                <button
                  className="btn small"
                  disabled={!!p.error}
                  onClick={() => void togglePlugin(p)}
                >
                  {p.enabled ? "Disable" : "Enable"}
                </button>
              </div>
            ))}
          </div>
        </Panel>
        <ResponsePanel />
        <PhonePanel />
      </div>
    </div>
  );
}
