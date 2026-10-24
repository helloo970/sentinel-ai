import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { ActionOutcome, Approval } from "../api/types";
import { refreshApprovals, refreshStatus } from "../hooks/useEvents";
import { formatTime } from "../lib/format";
import { askPin } from "../lib/pin";
import { pushToast, store, useStore } from "../state/store";
import { Chip, Panel } from "./Panel";

const RISK_TONE: Record<string, "ok" | "warn" | "crit" | "info"> = {
  read: "ok",
  low: "info",
  reversible: "warn",
  high: "crit",
  final: "crit",
};

async function decide(approval: Approval, approve: boolean) {
  try {
    if (approve) {
      let body: { pin?: string } = {};
      if (approval.requires_pin) {
        const pin = await askPin(approval.preview);
        if (!pin) return;
        body = { pin };
      }
      const result = await api.post<ActionOutcome>(`/approvals/${approval.id}/approve`, body);
      pushToast({
        title: result.status === "executed" ? "Done" : "Not done",
        message: result.message,
        severity: result.status === "executed" ? "info" : "warn",
        undoId: result.undo_id,
        expires: result.undo_id ? Date.now() / 1000 + 60 : null,
      });
    } else {
      await api.post(`/approvals/${approval.id}/reject`);
    }
  } catch (error) {
    pushToast({ title: "Error", message: String(error), severity: "critical" });
  }
  await Promise.all([refreshApprovals(), refreshStatus()]);
}

export function ApprovalCard({ approval }: { approval: Approval }) {
  const { t } = useTranslation();
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="spread">
        <strong>{approval.preview}</strong>
        <Chip tone={RISK_TONE[approval.risk] ?? "info"}>{t(`risk.${approval.risk}`)}</Chip>
      </div>
      <div className="dim" style={{ fontSize: "var(--fs-sm)" }}>
        {t("approvals.reason")}: {approval.reason} · {t("approvals.source")}: {approval.source} (
        {approval.actor}) · {formatTime(approval.created)}
      </div>
      {approval.tainted && <div className="warn">{t("approvals.tainted")}</div>}
      {approval.requires_pin && <div className="warn">{t("response.needsPin")}</div>}
      <pre className="code">{JSON.stringify(approval.params, null, 2)}</pre>
      <div className="row">
        <button className="btn primary" onClick={() => void decide(approval, true)}>
          {t("common.approve")}
        </button>
        <button className="btn ghost" onClick={() => void decide(approval, false)}>
          {t("common.reject")}
        </button>
      </div>
    </div>
  );
}

export function ApprovalsDrawer() {
  const { t } = useTranslation();
  const open = useStore((s) => s.approvalsOpen);
  const approvals = useStore((s) => s.approvals);
  if (!open) return null;
  return (
    <div className="drawer">
      <Panel
        title={t("approvals.title")}
        tone={approvals.length ? "warn" : undefined}
        actions={
          <button className="btn small ghost" onClick={() => store.set({ approvalsOpen: false })}>
            {t("common.close")}
          </button>
        }
      >
        {approvals.length === 0 && <div className="empty">{t("approvals.none")}</div>}
        {approvals.map((a) => (
          <ApprovalCard key={a.id} approval={a} />
        ))}
      </Panel>
    </div>
  );
}
