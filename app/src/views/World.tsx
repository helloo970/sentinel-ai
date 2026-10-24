import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { LiveNews } from "../components/LiveNews";
import { Globe3D } from "../components/Globe3D";
import { loadPrefs } from "../lib/prefs";
import { prefetchSpeech, speakAsync, stopSpeaking } from "../lib/speech";
import { pushToast, store, useStore } from "../state/store";

interface PlaceWeather {
  name: string;
  lat: number;
  lon: number;
  temperature: number | null;
  text: string;
}

interface TourCountry {
  code: string;
  name: string;
  lon: number;
  lat: number;
  zoom: number;
  headlines: string[];
  text: string;
  weather?: PlaceWeather;
}

interface Tour {
  intro: string;
  countries: TourCountry[];
  summarized: boolean;
  world: string[];
  home_weather?: PlaceWeather;
}

const FLY_MS = 1700;
let handledRequest = 0;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function World() {
  const { t } = useTranslation();
  const tts = useStore((s) => s.status?.voice.capabilities.tts ?? false);
  const tourRequest = useStore((s) => s.tourRequest);
  const tourMorning = useStore((s) => s.tourMorning);
  const [morningText, setMorningText] = useState<string | null>(null);
  const [tour, setTour] = useState<Tour | null>(null);
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [live, setLive] = useState(false);
  const run = useRef(0);

  const narrate = useCallback(
    async (text: string, id: number) => {
      if (run.current !== id) return;
      if (tts) {
        const result = await api
          .post<{ spoken: boolean }>("/voice/say", { text })
          .catch(() => null);
        if (result?.spoken) return;
      }
      await speakAsync(text);
    },
    [tts],
  );

  const stop = useCallback(() => {
    run.current += 1;
    stopSpeaking();
    setPlaying(false);
    setIndex(-1);
  }, []);

  const start = useCallback(
    async (morning = false, briefing = false) => {
      const id = ++run.current;
      setPlaying(true);
      setLive(false);
      setIndex(-1);
      setMorningText(null);
      try {
        if (morning) {
          const brief = await api.get<{ text: string }>("/briefing/morning");
          if (run.current !== id) return;
          setMorningText(brief.text);
          await narrate(brief.text, id);
        }
        const data = await api.get<Tour>("/news/tour");
        if (run.current !== id) return;
        setTour(data);
        if (data.countries[0]) prefetchSpeech(data.countries[0].text);
        await narrate(data.intro, id);
        for (let i = 0; i < data.countries.length; i++) {
          if (run.current !== id) return;
          setIndex(i);
          await sleep(FLY_MS);
          const country = data.countries[i];
          const next = data.countries[i + 1];
          if (next) prefetchSpeech(next.text);
          if (country) await narrate(country.text, id);
          await sleep(400);
        }
        // Briefing: continue to the markets
        if (run.current === id && briefing) {
          store.set({ view: "markets", viewTab: "briefing" });
          return;
        }
        // Then continue live: TRT Haber and tagesschau24 right in the app
        if (run.current === id && loadPrefs().liveNewsAfterTour) {
          await narrate("And now live: TRT Haber and tagesschau24.", id);
          if (run.current === id) setLive(true);
        }
      } catch (error) {
        pushToast({ title: t("world.title"), message: String(error), severity: "warn" });
      } finally {
        if (run.current === id) {
          setPlaying(false);
          setIndex(-1);
        }
      }
    },
    [narrate, t],
  );

  // A triple clap starts the world tour – each request exactly once
  useEffect(() => {
    if (!tourRequest || tourRequest === handledRequest) return;
    const timer = setTimeout(() => {
      handledRequest = tourRequest;
      void start(store.get().tourMorning);
    }, 50);
    return () => clearTimeout(timer);
  }, [tourRequest, tourMorning, start]);

  // Opened by voice: "What's going on in the world?" (tour) or "briefing"
  const viewTab = useStore((s) => s.viewTab);
  useEffect(() => {
    if (viewTab !== "tour" && viewTab !== "briefing") return;
    const timer = setTimeout(() => {
      store.set({ viewTab: null });
      void start(false, viewTab === "briefing");
    }, 50);
    return () => clearTimeout(timer);
  }, [viewTab, start]);

  useEffect(() => () => stop(), [stop]);

  const current = tour && index >= 0 ? tour.countries[index] : undefined;
  const weatherLabel = (w?: PlaceWeather) =>
    w && w.temperature !== null ? `${Math.round(w.temperature)}° ${w.text}` : null;
  const stops = useMemo(
    () => (tour?.countries ?? []).map((c) => ({ code: c.code, lat: c.lat, lon: c.lon })),
    [tour],
  );
  const focus = useMemo(
    () => (current ? { code: current.code, lat: current.lat, lon: current.lon } : null),
    [current],
  );

  return (
    <div className="world-full">
      <Globe3D focus={focus} stops={stops} />
      <div className="world-left">
        <div className="board-kicker">
          {t("world.kicker")} · {new Date().toLocaleDateString("en-US")}
        </div>
        <div className="world-country">{current?.name ?? t("world.title")}</div>
        {current?.weather && weatherLabel(current.weather) && (
          <span className="chip info">{weatherLabel(current.weather)}</span>
        )}
        <p className="world-text">
          {current?.text ?? morningText ?? tour?.intro ?? t("world.empty")}
        </p>
        {tour && (
          <div className="world-stops">
            {tour.countries.map((c, i) => (
              <span key={c.code} className={i === index ? "on" : i < index ? "done" : ""}>
                {c.name}
              </span>
            ))}
          </div>
        )}
        <div className="row">
          {playing ? (
            <button className="btn small danger" onClick={stop}>
              {t("world.stop")}
            </button>
          ) : (
            <button className="btn small primary" onClick={() => void start(false)}>
              {t("world.start")}
            </button>
          )}
          <span className="faint">{t("world.hint")}</span>
        </div>
        {tour && !tour.summarized && <div className="faint">{t("world.raw")}</div>}
      </div>
      <div className="world-right">
        {(current?.headlines ?? tour?.world ?? []).slice(0, 5).map((h) => (
          <div key={h} className="world-news">
            <span className="board-section">{current?.name ?? t("world.overview")}</span>
            <span>{h}</span>
          </div>
        ))}
      </div>
      {live && (
        <div className="world-live">
          <LiveNews onClose={() => setLive(false)} />
        </div>
      )}
    </div>
  );
}
