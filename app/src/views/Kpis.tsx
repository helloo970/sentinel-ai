import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { narrate } from "../lib/narrate";
import { store, useStore } from "../state/store";

interface Tile {
  key: string;
  label: string;
  value: number;
  unit: string;
  tone: "ok" | "warn" | "crit" | "info";
  detail: string;
}

/** KPI board: big numbers from every area. */
export function Kpis() {
  const { t } = useTranslation();
  const tab = useStore((s) => s.viewTab);
  const [tiles, setTiles] = useState<Tile[] | null>(null);

  useEffect(() => {
    let alive = true;
    const briefing = store.get().viewTab === "briefing";
    const load = () =>
      api.get<{ tiles: Tile[]; text: string }>("/kpis").then((result) => {
        if (alive) setTiles(result.tiles);
        return result;
      });
    load()
      .then(async (result) => {
        await narrate(result.text);
        // The briefing ends here: back to Jarvis
        if (alive && briefing) store.set({ view: "dashboard", viewTab: null });
      })
      .catch(() => undefined);
    const timer = window.setInterval(() => void load().catch(() => undefined), 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [tab]);

  return (
    <div className="board">
      <div className="board-kicker">{t("kpis.kicker")}</div>
      <h1 className="board-title">{t("kpis.title")}</h1>
      {!tiles && <div className="empty">{t("markets.loading")}</div>}
      <div className="kpi-grid">
        {tiles?.map((tile) => (
          <div key={tile.key} className={`kpi-tile ${tile.tone}`}>
            <div className="kpi-label">{tile.label}</div>
            <div className="kpi-value">
              {typeof tile.value === "number" ? tile.value.toLocaleString("en-US") : tile.value}
              {tile.unit && <small>{tile.unit}</small>}
            </div>
            <div className="kpi-detail">{tile.detail}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
