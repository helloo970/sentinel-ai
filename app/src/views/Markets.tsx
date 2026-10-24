import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { narrate } from "../lib/narrate";
import { formatPrice, sparkPoints } from "../lib/sparkline";
import { store, useStore } from "../state/store";

interface MarketItem {
  symbol: string;
  name: string;
  price: number | null;
  currency: string;
  change_pct: number | null;
  period: string;
  series: number[];
}

interface MarketData {
  updated: number;
  groups: { key: string; title: string; items: MarketItem[] }[];
  errors: string[];
}

const FEATURED = ["BTC", "DAX", "S&P 500"];

function Chart({ item, big = false }: { item: MarketItem; big?: boolean }) {
  const w = big ? 520 : 140;
  const h = big ? 170 : 38;
  const up = (item.change_pct ?? 0) >= 0;
  const points = sparkPoints(item.series, w, h);
  return (
    <svg
      className={`spark ${up ? "up" : "down"}`}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      {big && points && <polygon points={`0,${h} ${points} ${w},${h}`} className="area" />}
      {points && <polyline points={points} />}
    </svg>
  );
}

function Change({ value }: { value: number | null }) {
  if (value === null) return <span className="faint">–</span>;
  return (
    <span className={value >= 0 ? "mk-up" : "mk-down"}>
      {value >= 0 ? "▲" : "▼"}{" "}
      {Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 1 })} %
    </span>
  );
}

/** Markets: crypto, stocks & indices, currencies – Jarvis says what's moving. */
export function Markets() {
  const { t } = useTranslation();
  const tab = useStore((s) => s.viewTab);
  const [data, setData] = useState<MarketData | null>(null);

  useEffect(() => {
    let alive = true;
    const briefing = store.get().viewTab === "briefing";
    api
      .get<MarketData>("/markets")
      .then(async (result) => {
        if (!alive) return;
        setData(result);
        const summary = await api.get<{ text: string }>("/markets/summary");
        if (!alive) return;
        await narrate(summary.text);
        if (alive && briefing) store.set({ view: "kpis", viewTab: "briefing" });
      })
      .catch(() => {
        if (alive && briefing) store.set({ view: "kpis", viewTab: "briefing" });
      });
    return () => {
      alive = false;
    };
  }, [tab]);

  const all = data?.groups.flatMap((g) => g.items) ?? [];
  const featured = FEATURED.map((s) => all.find((i) => i.symbol === s)).filter(
    (i): i is MarketItem => Boolean(i),
  );
  return (
    <div className="board">
      <div className="board-kicker">
        {t("markets.kicker")} ·{" "}
        {data ? new Date(data.updated * 1000).toLocaleTimeString("en-US") : "…"}
      </div>
      <h1 className="board-title">{t("markets.title")}</h1>
      {!data && <div className="empty">{t("markets.loading")}</div>}
      {data && data.errors.length > 0 && (
        <div className="warn">
          {t("markets.unreachable")}: {data.errors.join(", ")}
        </div>
      )}
      <div className="mk-featured">
        {featured.map((item) => (
          <div key={item.symbol} className="mk-card big">
            <div className="mk-head">
              <span className="mk-sym">{item.symbol}</span>
              <Change value={item.change_pct} />
            </div>
            <div className="mk-price">{formatPrice(item.price, item.currency)}</div>
            <Chart item={item} big />
            <div className="faint">
              {item.name} · {item.period}
            </div>
          </div>
        ))}
      </div>
      <div className="mk-groups">
        {data?.groups.map((group) => (
          <div key={group.key} className="mk-group">
            <div className="board-section">{group.title}</div>
            {group.items.map((item) => (
              <div key={item.symbol} className="mk-row">
                <span className="mk-sym">{item.symbol}</span>
                <span className="mk-name faint">{item.name}</span>
                <Chart item={item} />
                <span className="mk-val">{formatPrice(item.price, item.currency)}</span>
                <Change value={item.change_pct} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
