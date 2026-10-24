import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import type { AgentInfo, AgentReport } from "../api/types";
import { Chip, Panel } from "../components/Panel";
import { useApi } from "../hooks/useApi";
import { refreshApprovals } from "../hooks/useEvents";
import { formatDateTime } from "../lib/format";
import { pushToast, useStore } from "../state/store";

export function Agents() {
  const { t } = useTranslation();
  const halted = useStore((s) => s.status?.halted);
  const reportsVersion = useStore((s) => s.reportsVersion);
  const agents = useApi<AgentInfo[]>("/agents", 5000);
  const reports = useApi<AgentReport[]>("/agents/reports", 0, [reportsVersion]);
  const [goal, setGoal] = useState("");
  const [running, setRunning] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  async function run(name: string) {
    setRunning(name);
    try {
      await api.post(`/agents/${name}/run`, name === "planner" ? { goal } : {});
      if (name === "planner") await refreshApprovals();
      await reports.reload();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ title: "Agent", message, severity: "warn" });
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("agents.title")}</h1>
        <button
          className="btn"
          onClick={async () => {
            await api.post("/reports/daily");
            await reports.reload();
          }}
        >
          {t("agents.dailyNow")}
        </button>
        <span className="dim">
          Agents only make suggestions – nothing runs without your approval.
        </span>
      </div>
      <div className="grid g4">
        {(agents.data ?? []).map((agent) => (
          <Panel key={agent.name} title={agent.title}>
            <p className="dim" style={{ minHeight: 44 }}>
              {agent.description}
            </p>
            {agent.name === "planner" && (
              <textarea
                className="textarea"
                rows={3}
                placeholder={t("agents.goal")}
                value={goal}
                maxLength={2000}
                onChange={(e) => setGoal(e.target.value)}
              />
            )}
            <button
              className="btn primary"
              disabled={
                halted ||
                running !== null ||
                agent.running ||
                (agent.name === "planner" && !goal.trim())
              }
              onClick={() => void run(agent.name)}
            >
              {agent.running || running === agent.name ? t("agents.running") : t("common.run")}
            </button>
          </Panel>
        ))}
      </div>
      <Panel title={t("agents.reports")}>
        <div className="list">
          {(reports.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
          {(reports.data ?? []).map((r) => (
            <div className="list-item" key={r.id}>
              <span className="stripe" />
              <div className="stack" style={{ gap: 6 }}>
                <button
                  className="btn ghost small"
                  style={{ justifyContent: "flex-start" }}
                  onClick={() => setOpen(open === r.id ? null : r.id)}
                >
                  {r.title}
                </button>
                {open === r.id && (
                  <pre className="code" style={{ maxHeight: 420 }}>
                    {r.body}
                  </pre>
                )}
              </div>
              <div className="row">
                <Chip tone="muted">{r.agent}</Chip>
                <span className="faint mono">{formatDateTime(r.ts)}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
