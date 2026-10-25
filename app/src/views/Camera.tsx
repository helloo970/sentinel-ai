import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, getBlob } from "../api/client";
import { formatAgo } from "../lib/format";

interface CameraState {
  configured: boolean;
  channels: { index: number; name: string }[];
  error: string | null;
  motion: boolean;
  last_motion?: { channel: number | null; name: string; at: number } | null;
}

const GRID_EVERY_MS = 2000;
const FOCUS_EVERY_MS = 700;

/** One live tile: fetches a still image again and again (never stored). */
function LiveImage({ index, every }: { index: number; every: number }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let url: string | null = null;
    let timer = 0;
    const load = async () => {
      try {
        const blob = await getBlob(`/camera/${index}/snapshot`, controller.signal);
        const next = URL.createObjectURL(blob);
        if (url) URL.revokeObjectURL(url);
        url = next;
        setSrc(next);
        setFailed(false);
      } catch {
        if (controller.signal.aborted) return;
        setFailed(true);
      }
      if (!controller.signal.aborted) timer = window.setTimeout(() => void load(), every);
    };
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
    };
  }, [index, every]);

  if (failed && !src) return <div className="cam-empty">●</div>;
  return src ? <img src={src} alt="" /> : <div className="cam-empty">…</div>;
}

export function Camera() {
  const { t } = useTranslation();
  const [state, setState] = useState<CameraState | null>(null);
  const [focus, setFocus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<CameraState>("/camera")
        .then((s) => alive && setState(s))
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  async function toggleMotion() {
    if (!state) return;
    const result = await api.put<{ motion: boolean }>("/camera/motion", {
      enabled: !state.motion,
    });
    setState({ ...state, motion: result.motion });
  }

  const focused = state?.channels.find((c) => c.index === focus);

  return (
    <div className="board">
      <div className="board-kicker">{t("camera.kicker")}</div>
      <h1 className="board-title">{t("camera.title")}</h1>
      {!state && <div className="empty">{t("markets.loading")}</div>}
      {state && !state.configured && (
        <div className="empty">
          {t("camera.notSetUp")}
          <div className="mono">sentinel camera login</div>
        </div>
      )}
      {state?.configured && (
        <div className="row">
          <button className="btn small" onClick={() => void toggleMotion()}>
            {state.motion ? t("camera.motionOn") : t("camera.motionOff")}
          </button>
          {state.last_motion && (
            <span className="faint">
              {t("camera.lastMotion", {
                name: state.last_motion.name,
                ago: formatAgo(state.last_motion.at),
              })}
            </span>
          )}
          {state.error && <span className="warn">{state.error}</span>}
        </div>
      )}
      {focused ? (
        <button className="cam-tile cam-focus" onClick={() => setFocus(null)}>
          <LiveImage index={focused.index} every={FOCUS_EVERY_MS} />
          <span className="cam-name">{focused.name}</span>
        </button>
      ) : (
        <div className="cam-grid">
          {state?.channels.map((c) => (
            <button key={c.index} className="cam-tile" onClick={() => setFocus(c.index)}>
              <LiveImage index={c.index} every={GRID_EVERY_MS} />
              <span className="cam-name">{c.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
