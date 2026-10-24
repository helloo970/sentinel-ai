import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { AuditEntry } from "../api/types";
import { Chip, Panel } from "../components/Panel";
import { useApi } from "../hooks/useApi";

const PAGE = 100;
const DECISION_TONE: Record<string, "ok" | "warn" | "crit" | "info" | "muted"> = {
  execute: "ok",
  approved: "ok",
  confirm: "warn",
  deny: "crit",
  rejected: "muted",
};

export function Audit() {
  const { t } = useTranslation();
  const [offset, setOffset] = useState(0);
  const entries = useApi<AuditEntry[]>(`/audit?limit=${PAGE}&offset=${offset}`, 10000, [offset]);
  const [valid, setValid] = useState<boolean | null>(null);

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("audit.title")}</h1>
        <div className="row">
          {valid !== null && (
            <Chip tone={valid ? "ok" : "crit"} dot>
              {valid ? t("audit.valid") : t("audit.invalid")}
            </Chip>
          )}
          <button
            className="btn"
            onClick={async () =>
              setValid((await api.get<{ valid: boolean }>("/audit/verify")).valid)
            }
          >
            {t("audit.verify")}
          </button>
        </div>
      </div>
      <Panel>
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 260px)" }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Actor</th>
                <th>Source</th>
                <th>Action</th>
                <th>Decision</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {(entries.data ?? []).map((e) => (
                <tr key={e.id}>
                  <td className="mono">{e.id}</td>
                  <td className="mono">{e.ts.replace("T", " ").slice(0, 19)}</td>
                  <td>{e.actor}</td>
                  <td className="dim">{e.source}</td>
                  <td className="mono">{e.action}</td>
                  <td>
                    <Chip tone={DECISION_TONE[e.decision] ?? "info"}>
                      {e.decision}
                      {e.result ? ` · ${e.result}` : ""}
                    </Chip>
                  </td>
                  <td className="faint" style={{ fontSize: "var(--fs-sm)" }}>
                    {e.detail}
                    {e.params !== "{}" && <div className="mono">{e.params}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="row">
          <button
            className="btn small ghost"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            {t("audit.newer")}
          </button>
          <button
            className="btn small ghost"
            disabled={(entries.data?.length ?? 0) < PAGE}
            onClick={() => setOffset(offset + PAGE)}
          >
            {t("audit.older")}
          </button>
        </div>
      </Panel>
    </div>
  );
}
