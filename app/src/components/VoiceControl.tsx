import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { refreshStatus } from "../hooks/useEvents";
import { useStore } from "../state/store";
import { Icon } from "./Icon";
import { Chip } from "./Panel";

/** Jarvis by voice: hold the microphone, speak, release. */
export function VoiceControl({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const voice = useStore((s) => s.status?.voice);
  const voiceState = useStore((s) => s.voiceState);
  const voiceChat = useStore((s) => s.voiceChat);
  const [ptt, setPtt] = useState(false);
  const [switching, setSwitching] = useState(false);

  const canListen = Boolean(voice?.capabilities.microphone && voice?.capabilities.stt);
  const active = Boolean(voice?.enabled && voice?.active);
  const [level, setLevel] = useState(0);

  // Live level: shows whether the microphone hears anything at all
  useEffect(() => {
    if (!active) return;
    let stop = false;
    const tick = async () => {
      const data = await api.get<{ level: number }>("/voice/level").catch(() => null);
      if (!stop && data) setLevel(data.level);
    };
    const id = window.setInterval(() => void tick(), 250);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [active]);

  async function enable(on: boolean) {
    setSwitching(true);
    try {
      await api.post("/voice/enable", { on });
      await refreshStatus();
    } finally {
      setSwitching(false);
    }
  }

  // Short click: listen until a pause. Hold: record until release.
  const [pressTimer, setPressTimer] = useState<number | null>(null);

  function down() {
    if (!active) return;
    const id = window.setTimeout(async () => {
      setPressTimer(null);
      const result = await api.post<{ recording: boolean }>("/voice/ptt/start").catch(() => null);
      setPtt(Boolean(result?.recording));
    }, 350);
    setPressTimer(id);
  }

  async function up() {
    if (pressTimer !== null) {
      window.clearTimeout(pressTimer);
      setPressTimer(null);
      await api.post("/voice/listen").catch(() => undefined);
      return;
    }
    if (!ptt) return;
    setPtt(false);
    await api.post("/voice/ptt/stop").catch(() => undefined);
  }

  const stateLabel = ptt ? t("voice.recording") : t(`voice.state.${voiceState}`, voiceState);

  return (
    <div className={`voice-control ${compact ? "compact" : ""}`}>
      <button
        type="button"
        className="voice-mic"
        data-active={ptt || voiceState === "listening"}
        disabled={!active}
        title={active ? t("voice.hold") : t("voice.off")}
        onPointerDown={down}
        onPointerUp={() => void up()}
        onPointerLeave={() => {
          if (ptt) void up();
        }}
      >
        <Icon name="mic" size={compact ? 28 : 40} />
      </button>
      <div className="stack" style={{ gap: 6, minWidth: 0 }}>
        <div className="row">
          <Chip tone={active ? "ok" : "muted"} dot>
            {active ? stateLabel : t("voice.offShort")}
          </Chip>
          {canListen && (
            <button
              type="button"
              className={`btn small ${active ? "ghost" : "primary"}`}
              disabled={switching}
              onClick={() => void enable(!voice?.enabled)}
            >
              {voice?.enabled ? t("voice.turnOff") : t("voice.turnOn")}
            </button>
          )}
        </div>
        {active && (
          <div className="voice-level" title={t("voice.level")}>
            <span style={{ width: `${Math.min(100, Math.round(level * 900))}%` }} />
          </div>
        )}
        {!canListen && <div className="warn">{t("voice.missing")}</div>}
        {voice?.error && <div className="warn">{voice.error}</div>}
        {active && !voice?.stt_ready && <div className="faint">{t("voice.loadingModel")}</div>}
        <div className="faint">{active ? t("voice.hint") : t("voice.offHint")}</div>
        {voiceChat && <pre className="code voice-chat">{voiceChat.text}</pre>}
      </div>
    </div>
  );
}
