import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, getBlob, readStream } from "../api/client";
import { formatAgo } from "../lib/format";
import { MjpegSplitter } from "../lib/mjpeg";

interface CameraState {
  configured: boolean;
  channels: { index: number; name: string }[];
  error: string | null;
  motion: boolean;
  video?: boolean;
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

/** Live video: MJPEG from the backend (ffmpeg), shows the newest frame only. */
function LiveVideo({ index, full }: { index: number; full: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const split = new MjpegSplitter();
    let url: string | null = null;
    let pending: Uint8Array | null = null;
    let frame = 0;
    const show = () => {
      frame = 0;
      if (!pending) return;
      const next = URL.createObjectURL(new Blob([pending as BlobPart], { type: "image/jpeg" }));
      pending = null;
      if (url) URL.revokeObjectURL(url);
      url = next;
      setSrc(next);
    };
    let frames = 0;
    let retry = 0;
    const start = () => {
      const before = frames;
      readStream(
        `/camera/${index}/video?full=${full}`,
        (chunk) => {
          const found = split.push(chunk);
          if (found.length === 0) return;
          frames += found.length;
          pending = found[found.length - 1] ?? null;
          if (!frame) frame = requestAnimationFrame(show);
        },
        controller.signal,
      )
        // The stream ended: reconnect if it was running, otherwise fall back to images
        .then(() => {
          if (controller.signal.aborted) return;
          if (frames > before) retry = window.setTimeout(start, 2000);
          else setFailed(true);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true);
        });
    };
    start();
    // Recorders allow only a few streams at once: no frame after 8 s -> still images
    const watchdog = window.setTimeout(() => {
      if (frames > 0) return;
      controller.abort();
      setFailed(true);
    }, 8000);
    return () => {
      window.clearTimeout(watchdog);
      controller.abort();
      window.clearTimeout(retry);
      cancelAnimationFrame(frame);
      if (url) URL.revokeObjectURL(url);
    };
  }, [index, full]);

  // Without video (no ffmpeg, stream off) the still images still work
  if (failed) return <LiveImage index={index} every={full ? FOCUS_EVERY_MS : GRID_EVERY_MS} />;
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
          {state.video === false && (
            <span className="faint">
              {t("camera.noVideo")} <span className="mono">winget install Gyan.FFmpeg</span>
            </span>
          )}
        </div>
      )}
      {focused ? (
        <button className="cam-tile cam-focus" onClick={() => setFocus(null)}>
          <LiveVideo index={focused.index} full />
          <span className="cam-name">{focused.name}</span>
        </button>
      ) : (
        <div className="cam-grid">
          {state?.channels.map((c) => (
            <button key={c.index} className="cam-tile" onClick={() => setFocus(c.index)}>
              <LiveVideo index={c.index} full={false} />
              <span className="cam-name">{c.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
