import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import type { ActionOutcome } from "../api/types";
import { ParcelPanel, PricePanel, ShoppingPanel } from "../components/EverydayPanels";
import { Chip, Panel } from "../components/Panel";
import { useApi } from "../hooks/useApi";
import { refreshApprovals } from "../hooks/useEvents";
import { formatDateTime } from "../lib/format";
import { pushToast, store, useStore } from "../state/store";

export interface Product {
  title: string;
  price: number | null;
  currency: string;
  shop: string;
  url: string;
  summary: string;
  specs: string[];
  availability: string;
}

export interface Order {
  id: number;
  ts: number;
  query: string;
  status: "proposed" | "opened";
  data: Product;
}

export function formatPrice(product: Product): string {
  if (product.price === null || product.price === undefined) return "Price unknown";
  return `${product.price.toFixed(2)} ${product.currency || "EUR"}`;
}

function ProductCard({ order, onOrder }: { order: Order; onOrder: (o: Order) => void }) {
  const { t } = useTranslation();
  const p = order.data;
  return (
    <Panel title={p.title} actions={<Chip tone="info">{p.shop}</Chip>}>
      <div className="spread">
        <span className="metric">
          <span className="value num">{formatPrice(p)}</span>
        </span>
        {p.availability && <Chip tone="ok">{p.availability}</Chip>}
      </div>
      {p.summary && <p>{p.summary}</p>}
      {p.specs.length > 0 && (
        <ul className="dim" style={{ margin: 0, paddingLeft: 18 }}>
          {p.specs.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      <div className="faint mono" style={{ overflowWrap: "anywhere" }}>
        {p.url}
      </div>
      <div className="row">
        <button className="btn primary" onClick={() => onOrder(order)}>
          {t("shop.order")}
        </button>
        {order.status === "opened" && <Chip tone="muted">{t("shop.opened")}</Chip>}
        <span className="faint">{formatDateTime(order.ts)}</span>
      </div>
    </Panel>
  );
}

export function Shop() {
  const { t } = useTranslation();
  const llm = useStore((s) => s.status?.llm.available ?? false);
  const orders = useApi<Order[]>("/shop/orders");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  async function research() {
    setBusy(true);
    try {
      await api.post<Order>("/shop/research", { query: query.trim() });
      setQuery("");
      await orders.reload();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ title: t("shop.title"), message, severity: "warn" });
    } finally {
      setBusy(false);
    }
  }

  async function order(o: Order) {
    const result = await api.post<ActionOutcome>(`/shop/orders/${o.id}/open`);
    if (result.status === "pending") {
      await refreshApprovals();
      store.set({ approvalsOpen: true });
    } else {
      pushToast({ title: t("shop.title"), message: result.message, severity: "warn" });
    }
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("shop.title")}</h1>
        <span className="dim">{t("shop.hint")}</span>
      </div>
      <div className="grid g2">
        <ShoppingPanel />
        <ParcelPanel />
      </div>
      <PricePanel />
      <Panel title={t("shop.search")}>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void research();
          }}
        >
          <input
            className="input grow"
            value={query}
            maxLength={200}
            placeholder={t("shop.placeholder")}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn primary" disabled={!llm || busy || query.trim().length < 3}>
            {busy ? t("common.loading") : t("shop.research")}
          </button>
        </form>
        {!llm && <div className="warn">{t("shop.needsKey")}</div>}
        <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
          {t("shop.safety")}
        </div>
      </Panel>
      <div className="grid g2">
        {(orders.data ?? []).map((o) => (
          <ProductCard key={o.id} order={o} onOrder={(x) => void order(x)} />
        ))}
      </div>
      {(orders.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
    </div>
  );
}
