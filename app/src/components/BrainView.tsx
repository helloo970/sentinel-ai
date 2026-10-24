import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import {
  nodeColor,
  originLabel,
  partCounts,
  PART_COLORS,
  TUNNELS,
  type BrainGraphData,
  type BrainNode,
} from "../lib/brain";
import { useStore } from "../state/store";
import { BrainGraph } from "./BrainGraph";
import { Chip } from "./Panel";

interface BrainStatus {
  nodes: number;
  edges: number;
  queue: number;
  ai_today: number;
  ai_daily: number;
  backup_password: boolean;
  backup_server: boolean;
  backup_last: number | null;
  backup_error: string | null;
}

type NodeDetail = BrainNode & { neighbors: BrainNode[] };

/** Jarvis' brain: 3D knowledge network with details on the selected thought. */
export function BrainView() {
  const { t } = useTranslation();
  const version = useStore((s) => s.brainVersion);
  const active = useStore((s) => s.brainActive);
  const focus = useStore((s) => s.brainFocus);
  const [data, setData] = useState<BrainGraphData | null>(null);
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [selected, setSelected] = useState<NodeDetail | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    api
      .get<BrainGraphData>("/brain/graph")
      .then(setData)
      .catch(() => undefined);
    api
      .get<BrainStatus>("/brain/status")
      .then(setStatus)
      .catch(() => undefined);
  }, [version]);

  const select = useCallback((nodeId: number | null) => {
    setConfirmDelete(false);
    if (nodeId === null) {
      setSelected(null);
      return;
    }
    api
      .get<NodeDetail>(`/brain/nodes/${nodeId}`)
      .then(setSelected)
      .catch(() => setSelected(null));
  }, []);

  async function remove() {
    if (!selected) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    await api.del(`/brain/nodes/${selected.id}`).catch(() => undefined);
    setSelected(null);
    setConfirmDelete(false);
  }

  const categories = new Map(data?.categories.map((c) => [c.key, c.label]));
  const counts = data ? partCounts(data) : null;
  return (
    <div className="brain">
      <BrainGraph data={data} active={active} focus={focus} onSelect={select} />
      <div className="brain-overlay">
        <div className="brain-kicker">
          JARVIS · SENTINEL · {new Date().toLocaleDateString("en-US")}
        </div>
        <h3 className="brain-title">{t("brain.heading")}</h3>
        <div className="brain-section">{t("brain.parts")}</div>
        <ul className="brain-parts">
          {(["gehirn", "stimme", "hand", "quellen"] as const).map((part) => (
            <li key={part}>
              <span className="dot" style={{ background: PART_COLORS[part] }} />
              {t(`brain.part.${part}`)}
              <b>{counts ? counts[part].toLocaleString("en-US") : "–"}</b>
            </li>
          ))}
          <li>
            <span className="dot" style={{ background: "#ffffff" }} />
            {t("brain.part.ich")}
            <b>{counts?.ich ?? "–"}</b>
          </li>
        </ul>
        <div className="brain-section">{t("brain.tunnel")}</div>
        <ul className="brain-tunnels">
          {TUNNELS.map((tunnel) => (
            <li key={tunnel.key}>
              {t(`brain.part.${tunnel.from}`)} → {t(`brain.part.${tunnel.to}`)}
              <i>{t(`brain.flow.${tunnel.key}`)}</i>
            </li>
          ))}
        </ul>
        {status && (
          <div className="brain-meta">
            <span>
              {t("brain.learning", {
                today: status.ai_today,
                daily: status.ai_daily,
                queue: status.queue,
              })}
            </span>
            <Chip tone={status.backup_error ? "warn" : status.backup_last ? "ok" : "muted"} dot>
              {status.backup_error
                ? t("brain.backupError")
                : status.backup_last
                  ? t("brain.backupOk", {
                      time: new Date(status.backup_last * 1000).toLocaleString("en-US"),
                    })
                  : status.backup_password && status.backup_server
                    ? t("brain.backupPending")
                    : t("brain.backupOff")}
            </Chip>
          </div>
        )}
      </div>
      <div className="brain-caption">
        <div className="brain-section">{t("brain.captionKicker")}</div>
        <h3>{t("brain.captionTitle")}</h3>
        <p>{t("brain.captionText")}</p>
      </div>
      <div className="brain-hint">{t("brain.hint")}</div>
      {selected && (
        <aside className="brain-detail">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong style={{ color: nodeColor(selected.category) }}>{selected.label}</strong>
            <button className="btn ghost small" onClick={() => select(null)} aria-label="close">
              ✕
            </button>
          </div>
          <div className="muted small">
            {categories.get(selected.category) ?? selected.category} ·{" "}
            {originLabel(selected.origin)}
          </div>
          {selected.summary && <p>{selected.summary}</p>}
          {selected.neighbors.length > 0 && (
            <div className="brain-links">
              {selected.neighbors.slice(0, 12).map((n) => (
                <button key={n.id} className="chip info" onClick={() => select(n.id)}>
                  {n.label}
                </button>
              ))}
            </div>
          )}
          <div className="muted small" title={selected.source}>
            {t("brain.source")}: {selected.source}
          </div>
          <button
            className={`btn small ${confirmDelete ? "danger" : "ghost"}`}
            onClick={() => void remove()}
          >
            {confirmDelete ? t("brain.confirmForget") : t("brain.forget")}
          </button>
        </aside>
      )}
    </div>
  );
}
