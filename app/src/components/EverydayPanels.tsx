import { useState } from "react";
import { useTranslation } from "react-i18next";
import { runAction, useApi } from "../hooks/useApi";
import { formatAgo, formatDateTime } from "../lib/format";
import { Chip, Panel } from "./Panel";

interface ShoppingItem {
  id: number;
  text: string;
}

interface PriceWatch {
  id: number;
  url: string;
  shop: string;
  title: string;
  target: number | null;
  currency: string;
  last_price: number | null;
  last_check: number;
  last_error: string | null;
  history: { ts: number; price: number }[];
}

interface Parcel {
  carrier: string;
  carrier_name: string;
  number: string;
  status: "today" | "pickup" | "shipped" | "unknown" | "delivered";
  subject: string;
  sender: string;
  date: number | null;
}

const STATUS_TONE: Record<Parcel["status"], "ok" | "warn" | "info" | "muted"> = {
  today: "warn",
  pickup: "warn",
  shipped: "info",
  unknown: "muted",
  delivered: "ok",
};

export function euro(value: number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined) return "–";
  return `${value.toFixed(2)} ${currency}`;
}

/** Small price history (latest checks). */
export function Sparkline({ points }: { points: { price: number }[] }) {
  if (points.length < 2) return null;
  const values = points.map((p) => p.price);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const path = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 24}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 30" width="100" height="30" aria-hidden="true">
      <polyline points={path} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
    </svg>
  );
}

export function ShoppingPanel() {
  const { t } = useTranslation();
  const items = useApi<ShoppingItem[]>("/shop/list", 15000);
  const [text, setText] = useState("");

  async function add() {
    if (!text.trim()) return;
    await runAction("add_shopping_item", { text: text.trim() });
    setText("");
    await items.reload();
  }

  async function remove(item: ShoppingItem) {
    await runAction("remove_shopping_item", { text: item.text });
    await items.reload();
  }

  return (
    <Panel
      title={t("everyday.shopping")}
      actions={<Chip tone="info">{(items.data ?? []).length}</Chip>}
    >
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <input
          className="input"
          value={text}
          maxLength={200}
          placeholder={t("everyday.shoppingPlaceholder")}
          onChange={(e) => setText(e.target.value)}
        />
        <button className="btn small primary">{t("common.add")}</button>
      </form>
      <div className="list">
        {(items.data ?? []).length === 0 && <div className="empty">{t("everyday.empty")}</div>}
        {(items.data ?? []).map((item) => (
          <div className="list-item" key={item.id}>
            <span className="stripe" />
            <span>{item.text}</span>
            <button className="btn small ghost" onClick={() => void remove(item)}>
              {t("everyday.done")}
            </button>
          </div>
        ))}
      </div>
      <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
        {t("everyday.shoppingHint")}
      </div>
    </Panel>
  );
}

export function PricePanel() {
  const { t } = useTranslation();
  const watches = useApi<PriceWatch[]>("/shop/prices", 60000);
  const [url, setUrl] = useState("");
  const [target, setTarget] = useState("");

  async function add() {
    if (!url.trim()) return;
    const value = Number(target.replace(",", "."));
    await runAction("watch_price", {
      url: url.trim(),
      ...(target && value > 0 ? { target: value } : {}),
    });
    setUrl("");
    setTarget("");
    await watches.reload();
  }

  return (
    <Panel title={t("everyday.prices")}>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <input
          className="input"
          value={url}
          placeholder={t("everyday.priceUrl")}
          onChange={(e) => setUrl(e.target.value)}
        />
        <input
          className="input"
          value={target}
          inputMode="decimal"
          placeholder={t("everyday.priceTarget")}
          onChange={(e) => setTarget(e.target.value)}
          style={{ width: 110 }}
        />
        <button className="btn small primary">{t("common.add")}</button>
      </form>
      <div className="list">
        {(watches.data ?? []).length === 0 && <div className="empty">{t("everyday.empty")}</div>}
        {(watches.data ?? []).map((w) => {
          const reached = w.target !== null && w.last_price !== null && w.last_price <= w.target;
          return (
            <div className="list-item" key={w.id}>
              <span className={`stripe ${reached ? "ok" : w.last_error ? "warn" : ""}`} />
              <div className="stack" style={{ gap: 2 }}>
                <span>{w.title || w.shop}</span>
                <span className="faint">
                  {w.shop}
                  {w.target !== null ? ` · ${t("everyday.target")} ${euro(w.target)}` : ""}
                  {w.last_check ? ` · ${formatAgo(w.last_check)}` : ""}
                </span>
                {w.last_error && <span className="warn">{w.last_error}</span>}
              </div>
              <div className="row">
                <Sparkline points={w.history} />
                <Chip tone={reached ? "ok" : "info"}>{euro(w.last_price, w.currency)}</Chip>
                <button
                  className="btn small ghost"
                  onClick={async () => {
                    await runAction("unwatch_price", { id: w.id });
                    await watches.reload();
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          );
        })}
      </div>
      <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
        {t("everyday.priceHint")}
      </div>
    </Panel>
  );
}

export function ParcelPanel() {
  const { t } = useTranslation();
  const data = useApi<{ configured: boolean; error?: string; parcels: Parcel[] }>(
    "/mail/parcels",
    900000,
  );
  const parcels = data.data?.parcels ?? [];
  return (
    <Panel title={t("everyday.parcels")}>
      {data.data && !data.data.configured && (
        <div className="dim">{t("everyday.parcelsMissing")}</div>
      )}
      {data.data?.error && <div className="warn">{data.data.error}</div>}
      <div className="list">
        {data.data?.configured && parcels.length === 0 && !data.data.error && (
          <div className="empty">{t("everyday.noParcels")}</div>
        )}
        {parcels.map((p) => (
          <div className="list-item" key={`${p.carrier_name}-${p.number}`}>
            <span className={`stripe ${STATUS_TONE[p.status] === "warn" ? "warn" : ""}`} />
            <div className="stack" style={{ gap: 2 }}>
              <span>{p.subject || p.sender}</span>
              <span className="faint mono">
                {p.carrier_name} · {p.number} · {formatDateTime(p.date)}
              </span>
            </div>
            <div className="row">
              <Chip tone={STATUS_TONE[p.status]}>{t(`everyday.status.${p.status}`)}</Chip>
              <button
                className="btn small"
                onClick={() =>
                  void runAction("open_tracking", { carrier: p.carrier, number: p.number })
                }
              >
                {t("everyday.track")}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
