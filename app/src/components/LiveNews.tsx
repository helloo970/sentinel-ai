import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Panel } from "./Panel";

/** Fixed live streams of the broadcasters (HLS). Only these hosts are allowed in the CSP. */
export const LIVE_STREAMS = [
  { id: "trt", title: "TRT Haber", url: "https://tv-trthaber.medya.trt.com.tr/master.m3u8" },
  {
    id: "tagesschau",
    title: "tagesschau24",
    url: "https://tagesschau.akamaized.net/hls/live/2020115/tagesschau/tagesschau_1/master.m3u8",
  },
] as const;

type StreamId = (typeof LIVE_STREAMS)[number]["id"];

function Player({ url, muted }: { url: string; muted: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = video.current;
    if (!element) return;
    setError(null);
    if (!Hls.isSupported()) {
      element.src = url; // Fallback in case the browser can play HLS itself
      void element.play().catch(() => undefined);
      return;
    }
    const hls = new Hls({ enableWorker: false, lowLatencyMode: true });
    hls.loadSource(url);
    hls.attachMedia(element);
    hls.on(Hls.Events.MANIFEST_PARSED, () => void element.play().catch(() => undefined));
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) setError(data.type);
    });
    return () => hls.destroy();
  }, [url]);

  useEffect(() => {
    if (video.current) video.current.muted = muted;
  }, [muted]);

  return (
    <div className="live-player">
      <video ref={video} playsInline autoPlay muted={muted} controls />
      {error && <div className="warn">Stream not reachable ({error})</div>}
    </div>
  );
}

/** Live news right in the app: one channel with sound, the other muted next to it. */
export function LiveNews({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [audio, setAudio] = useState<StreamId>("trt");
  return (
    <Panel
      title={t("world.live")}
      actions={
        <button className="btn small ghost" onClick={onClose}>
          {t("common.close")}
        </button>
      }
    >
      <div className="live-grid">
        {LIVE_STREAMS.map((stream) => (
          <div key={stream.id} className="stack" style={{ gap: 6 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b>{stream.title}</b>
              <button
                className={`btn small ${audio === stream.id ? "primary" : "ghost"}`}
                onClick={() => setAudio(stream.id)}
              >
                {audio === stream.id ? t("world.soundOn") : t("world.soundHere")}
              </button>
            </div>
            <Player url={stream.url} muted={audio !== stream.id} />
          </div>
        ))}
      </div>
    </Panel>
  );
}
