import { TodayPanel } from "../components/TodayPanel";
import { useTranslation } from "react-i18next";
import type { Alert, Device, SystemInfo } from "../api/types";
import { AutonomyControl } from "../components/AutonomyControl";
import { HudCore } from "../components/HudCore";
import { VoiceControl } from "../components/VoiceControl";
import { Chip, Metric, Panel } from "../components/Panel";
import { useApi } from "../hooks/useApi";
import { deriveCoreState, deriveMood } from "../lib/coreState";
import { formatAgo, percentTone, severityTone } from "../lib/format";
import { useStore } from "../state/store";

export function Dashboard() {
  const { t } = useTranslation();
  const status = useStore((s) => s.status);
  const voiceState = useStore((s) => s.voiceState);
  const alertsVersion = useStore((s) => s.alertsVersion);
  const system = useApi<SystemInfo>("/system", 5000);
  const alerts = useApi<Alert[]>("/security/alerts?status=new&limit=6", 0, [alertsVersion]);
  const network = useApi<{ subnet: string | null; devices: Device[] }>("/network/devices", 60000);
  const sys = system.data;
  const untrusted = network.data?.devices.filter((d) => !d.trusted).length ?? 0;
  const securityTone =
    (status?.alerts.critical ?? 0) > 0
      ? "crit"
      : (status?.alerts.warn ?? 0) > 0
        ? "warn"
        : undefined;

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("dashboard.title")}</h1>
        <span className="dim">{t("dashboard.greeting", { name: status?.user_name || "Sir" })}</span>
      </div>

      <div className="grid dash-grid">
        <div className="stack">
          <Panel title={t("dashboard.system")}>
            <div className="grid g2">
              <Metric
                label={t("dashboard.cpu")}
                value={`${sys?.cpu_percent ?? "–"} %`}
                percent={sys?.cpu_percent}
                tone={percentTone(sys?.cpu_percent ?? 0)}
              />
              <Metric
                label={t("dashboard.ram")}
                value={`${sys?.memory_percent ?? "–"} %`}
                percent={sys?.memory_percent}
                tone={percentTone(sys?.memory_percent ?? 0)}
                sub={sys ? `${sys.memory_total_gb} GB` : undefined}
              />
              <Metric
                label={t("dashboard.disk")}
                value={`${sys?.disk_percent ?? "–"} %`}
                percent={sys?.disk_percent}
                tone={percentTone(sys?.disk_percent ?? 0, 80, 92)}
                sub={sys ? `${sys.disk_free_gb} GB free` : undefined}
              />
              <Metric
                label={t("dashboard.uptime")}
                value={`${sys?.uptime_h ?? "–"} h`}
                sub={sys ? `${sys.process_count} processes` : undefined}
              />
            </div>
          </Panel>
        </div>

        <div className="stack dash-core" style={{ alignItems: "center" }}>
          <HudCore
            state={deriveCoreState(status, voiceState, voiceState === "thinking")}
            mood={deriveMood(status)}
          />
          <Panel title={t("voice.talk")} className="dash-voice">
            <VoiceControl compact />
          </Panel>
          <div className="stack" style={{ alignItems: "center", gap: 6 }}>
            <AutonomyControl />
            <span className="dim" style={{ fontSize: "var(--fs-sm)" }}>
              {t("autonomy.budget", {
                count: status?.autonomous_last_hour ?? 0,
                limit: status?.autonomous_limit ?? 10,
              })}
            </span>
          </div>
        </div>

        <div className="stack">
          <Panel title={t("dashboard.security")} tone={securityTone}>
            <div className="grid g3">
              <Metric
                label="Critical"
                value={
                  <span className={status?.alerts.critical ? "crit" : ""}>
                    {status?.alerts.critical ?? 0}
                  </span>
                }
              />
              <Metric
                label="Warning"
                value={
                  <span className={status?.alerts.warn ? "warn" : ""}>
                    {status?.alerts.warn ?? 0}
                  </span>
                }
              />
              <Metric label="Info" value={status?.alerts.info ?? 0} />
            </div>
            <div className="dim" style={{ fontSize: "var(--fs-sm)" }}>
              {t("dashboard.lastScan")}: {formatAgo(status?.monitor.last_scan)} ·{" "}
              {t("dashboard.sysmon")}: {status?.monitor.sysmon ?? "–"}
            </div>
          </Panel>
          <Panel title={t("dashboard.network")}>
            <div className="grid g2">
              <Metric
                label={t("dashboard.devices")}
                value={network.data?.devices.length ?? "–"}
                sub={network.data?.subnet ?? "–"}
              />
              <Metric
                label="Unconfirmed"
                value={<span className={untrusted ? "warn" : ""}>{untrusted}</span>}
              />
            </div>
          </Panel>
        </div>
      </div>

      <TodayPanel />
      <div className="grid">
        <Panel
          title={t("dashboard.recentAlerts")}
          actions={
            <button className="btn small ghost" onClick={() => void alerts.reload()}>
              {t("common.refresh")}
            </button>
          }
        >
          <div className="list">
            {(alerts.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
            {(alerts.data ?? []).map((a) => (
              <div className="list-item" key={a.id}>
                <span className={`stripe ${severityTone(a.severity)}`} />
                <div>
                  <div>{a.title}</div>
                  <div className="faint mono">
                    {a.rule} · {formatAgo(a.ts)}
                  </div>
                </div>
                <Chip tone={severityTone(a.severity)}>{a.severity}</Chip>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
