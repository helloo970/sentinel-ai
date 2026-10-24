import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { refreshStatus } from "../hooks/useEvents";
import { useApi } from "../hooks/useApi";
import { useStore } from "../state/store";
import { Chip, Panel } from "./Panel";

interface InputDevice {
  index: number;
  name: string;
  api: string;
  rate: number;
  default: boolean;
}

interface DeviceList {
  devices: InputDevice[];
  selected: { name: string; api: string } | null;
  current?: string;
  error?: string;
}

/** Settings → voice: choose and test the microphone, voice on/off. */
export function MicSettings() {
  const { t } = useTranslation();
  const voice = useStore((s) => s.status?.voice);
  const list = useApi<DeviceList>("/voice/devices");
  const [testing, setTesting] = useState(false);
  const [level, setLevel] = useState(0);
  const [peak, setPeak] = useState(0);

  useEffect(() => {
    if (!testing) return;
    setPeak(0);
    const id = window.setInterval(async () => {
      const data = await api.get<{ level: number }>("/voice/level").catch(() => null);
      if (data) {
        setLevel(data.level);
        setPeak((p) => Math.max(p, data.level));
      }
    }, 150);
    const stop = window.setTimeout(() => setTesting(false), 8000);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(stop);
    };
  }, [testing]);

  async function choose(value: string) {
    const device = list.data?.devices.find((d) => `${d.api}|${d.name}` === value);
    await api.post("/voice/device", device ? { name: device.name, api: device.api } : {});
    await Promise.all([list.reload(), refreshStatus()]);
  }

  async function toggle() {
    await api.post("/voice/enable", { on: !voice?.enabled });
    await refreshStatus();
  }

  const selected = list.data?.selected
    ? `${list.data.selected.api}|${list.data.selected.name}`
    : "";
  const heard = peak > 0.002;
  return (
    <Panel
      title={t("mic.title")}
      actions={
        <Chip tone={voice?.enabled ? "ok" : "muted"} dot>
          {voice?.enabled ? t("mic.on") : t("mic.off")}
        </Chip>
      }
    >
      <div className="row">
        <button className={`btn small ${voice?.enabled ? "ghost" : "primary"}`} onClick={toggle}>
          {voice?.enabled ? t("voice.turnOff") : t("voice.turnOn")}
        </button>
      </div>
      <label className="stack" style={{ gap: 4 }}>
        <span className="label">{t("mic.device")}</span>
        <select className="select" value={selected} onChange={(e) => void choose(e.target.value)}>
          <option value="">{t("mic.default")}</option>
          {(list.data?.devices ?? []).map((d) => (
            <option key={`${d.api}|${d.name}`} value={`${d.api}|${d.name}`}>
              {d.name}
              {d.default ? ` (${t("mic.windowsDefault")})` : ""}
            </option>
          ))}
        </select>
      </label>
      {list.data?.current && (
        <div className="faint">
          {t("mic.current")}: {list.data.current}
        </div>
      )}
      <div className="row">
        <button
          className="btn small"
          disabled={!voice?.enabled || testing}
          onClick={() => setTesting(true)}
        >
          {testing ? t("mic.testing") : t("mic.test")}
        </button>
        <button className="btn small ghost" onClick={() => void list.reload()}>
          {t("mic.refresh")}
        </button>
      </div>
      {(testing || peak > 0) && (
        <div className="stack" style={{ gap: 6 }}>
          <div className="voice-level">
            <span style={{ width: `${Math.min(100, Math.round(level * 900))}%` }} />
          </div>
          {!testing && (
            <div className={heard ? "ok" : "warn"}>{heard ? t("mic.heard") : t("mic.silent")}</div>
          )}
        </div>
      )}
      <div className="faint">
        {t("mic.stt")}:{" "}
        {voice?.stt_provider === "elevenlabs" ? "ElevenLabs Scribe" : t("mic.sttLocal")}
      </div>
      {voice?.stt_error && <div className="warn">{voice.stt_error}</div>}
      {voice?.error && <div className="warn">{voice.error}</div>}
      <details>
        <summary className="dim">{t("mic.helpTitle")}</summary>
        <ol className="dim" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li>{t("mic.help1")}</li>
          <li>{t("mic.help2")}</li>
          <li>{t("mic.help3")}</li>
          <li>{t("mic.help4")}</li>
        </ol>
      </details>
    </Panel>
  );
}
