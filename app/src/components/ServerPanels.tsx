import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { countryName, toMapCode } from "../lib/countries";
import { formatAgo, formatDateTime } from "../lib/format";
import { Chip, Metric, Panel } from "./Panel";
import { WorldMap } from "./WorldMap";

interface Container {
  name: string;
  image: string;
  state: string;
  status: string;
  healthy: boolean | null;
}

interface Pihole {
  configured: boolean;
  error?: string;
  queries_total?: number;
  queries_blocked?: number;
  percent_blocked?: number;
  clients_active?: number;
  top_blocked?: { domain: string; count: number }[];
  top_clients?: { ip: string; name: string; count: number }[];
}

interface BackupEntry {
  path: string;
  exists: boolean;
  files?: number;
  bytes?: number;
  newest?: number | null;
}

interface Backups {
  available: boolean;
  stale?: boolean;
  reason?: string;
  paths: BackupEntry[];
}

export interface ServerStatus {
  configured: boolean;
  last_poll: number | null;
  error: string | null;
  relay?: {
    enabled: boolean;
    supported: boolean | null;
    last_ok: number | null;
    error: string | null;
  };
  data: {
    status?: Record<string, unknown>;
    services?: { services: { name: string; state: string }[] };
    auth_failures?: { failures_1h: number; top_sources: { ip: string; count: number }[] };
    updates?: { pending: number; security: number; reboot_required: boolean };
    docker?: { available: boolean; stale?: boolean; containers: Container[]; reason?: string };
    pihole?: Pihole;
    backups?: Backups;
  } | null;
}

interface MetricPoint {
  ts: number;
  load1: number;
  cpu_count: number;
  memory_percent: number;
  disk_percent: number;
  auth_failures: number;
}

const CONTAINER_TONE: Record<string, "ok" | "warn" | "crit" | "muted"> = {
  running: "ok",
  restarting: "crit",
  dead: "crit",
  exited: "muted",
  paused: "warn",
};

export function ServerOverview({
  server,
  onRefresh,
}: {
  server: ServerStatus | null;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const data = server?.data;
  const status = data?.status as Record<string, number | string> | undefined;
  return (
    <Panel
      title={t("network.server")}
      actions={
        <>
          {server?.last_poll && <span className="faint">{formatAgo(server.last_poll)}</span>}
          <button className="btn small" onClick={onRefresh}>
            {t("common.refresh")}
          </button>
        </>
      }
    >
      {!server?.configured && <div className="dim">{t("network.serverMissing")}</div>}
      {server?.error && <div className="warn">{server.error}</div>}
      {status && (
        <div className="grid g4">
          <Metric
            label="System"
            value={<span style={{ fontSize: "var(--fs-md)" }}>{String(status.os)}</span>}
            sub={`${status.uptime_h} h online`}
          />
          <Metric
            label="RAM"
            value={`${status.memory_percent} %`}
            percent={Number(status.memory_percent)}
          />
          <Metric
            label="Disk"
            value={`${status.disk_percent} %`}
            percent={Number(status.disk_percent)}
          />
          <Metric label={t("server.sshFails")} value={data?.auth_failures?.failures_1h ?? 0} />
        </div>
      )}
      {data?.services && (
        <div className="row">
          {data.services.services.map((s) => (
            <Chip key={s.name} tone={s.state === "active" ? "ok" : "crit"} dot>
              {s.name}: {s.state}
            </Chip>
          ))}
          {data.updates && (
            <Chip tone={data.updates.security ? "warn" : "ok"}>
              {t("server.updates", {
                pending: data.updates.pending,
                security: data.updates.security,
              })}
            </Chip>
          )}
          {data.updates?.reboot_required && <Chip tone="warn">{t("server.reboot")}</Chip>}
        </div>
      )}
      {data?.docker && (
        <>
          <h3>Docker</h3>
          {!data.docker.available && <div className="faint">{t("server.dockerMissing")}</div>}
          {data.docker.stale && <div className="warn">{t("server.dockerStale")}</div>}
          <div className="row">
            {data.docker.containers.map((c) => (
              <Chip
                key={c.name}
                tone={c.healthy === false ? "crit" : (CONTAINER_TONE[c.state] ?? "info")}
                dot
              >
                {c.name} · {c.state}
                {c.healthy === false ? " · unhealthy" : ""}
              </Chip>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

function formatBytes(value = 0): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

/** Backup watcher and phone view (both via the server agent). */
export function BackupPanel({ server }: { server: ServerStatus | null }) {
  const { t } = useTranslation();
  if (!server?.configured) return null;
  const backups = server.data?.backups;
  const relay = server.relay;
  const now = Date.now() / 1000;
  return (
    <Panel
      title={t("server.backups")}
      actions={
        relay?.enabled ? (
          <Chip tone={relay.supported && !relay.error ? "ok" : "warn"} dot>
            {relay.supported && !relay.error ? t("server.phoneOn") : t("server.phoneOff")}
          </Chip>
        ) : null
      }
    >
      {relay?.error && <div className="warn">{relay.error}</div>}
      {!backups && <div className="dim">{t("server.backupsUpdate")}</div>}
      {backups && !backups.available && <div className="dim">{t("server.backupsMissing")}</div>}
      {backups?.stale && <div className="warn">{t("server.backupsStale")}</div>}
      <div className="list">
        {(backups?.paths ?? []).map((b) => {
          const hours = b.newest ? (now - b.newest) / 3600 : null;
          const bad = !b.exists || hours === null || hours > 26;
          return (
            <div className="list-item" key={b.path}>
              <span className={`stripe ${bad ? "warn" : "ok"}`} />
              <div className="stack" style={{ gap: 2 }}>
                <span className="mono">{b.path}</span>
                <span className="faint">
                  {b.exists
                    ? t("server.backupLine", {
                        files: b.files ?? 0,
                        size: formatBytes(b.bytes),
                      })
                    : t("server.backupGone")}
                </span>
              </div>
              <Chip tone={bad ? "warn" : "ok"}>{b.newest ? formatAgo(b.newest) : "–"}</Chip>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function PiholePanel({ pihole }: { pihole: Pihole | undefined }) {
  const { t } = useTranslation();
  if (!pihole) return null;
  return (
    <Panel title="Pi-hole">
      {!pihole.configured && <div className="dim">{t("server.piholeMissing")}</div>}
      {pihole.error && <div className="warn">{pihole.error}</div>}
      {pihole.configured && !pihole.error && (
        <>
          <div className="grid g3">
            <Metric label={t("server.queries")} value={pihole.queries_total ?? 0} />
            <Metric
              label={t("server.blocked")}
              value={`${pihole.percent_blocked ?? 0} %`}
              percent={pihole.percent_blocked}
              sub={`${pihole.queries_blocked ?? 0}`}
            />
            <Metric label={t("server.clients")} value={pihole.clients_active ?? 0} />
          </div>
          <div className="grid g2">
            <div>
              <h3>{t("server.topBlocked")}</h3>
              <table>
                <tbody>
                  {(pihole.top_blocked ?? []).map((d) => (
                    <tr key={d.domain}>
                      <td className="mono">{d.domain}</td>
                      <td className="num">{d.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3>{t("server.topClients")}</h3>
              <table>
                <tbody>
                  {(pihole.top_clients ?? []).map((c) => (
                    <tr key={c.ip}>
                      <td>{c.name || <span className="mono">{c.ip}</span>}</td>
                      <td className="num">{c.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}

const SERIES: { key: "memory" | "disk" | "load"; color: string; label: string }[] = [
  { key: "memory", color: "var(--accent)", label: "RAM" },
  { key: "disk", color: "var(--warn)", label: "Disk" },
  { key: "load", color: "var(--ok)", label: "Load" },
];

export function seriesValue(point: MetricPoint, key: "memory" | "disk" | "load"): number {
  if (key === "memory") return point.memory_percent;
  if (key === "disk") return point.disk_percent;
  return Math.min(100, (point.load1 / Math.max(1, point.cpu_count)) * 100);
}

export function HistoryPanel() {
  const { t } = useTranslation();
  const history = useApi<MetricPoint[]>("/server/history?days=7", 300000);
  const points = history.data ?? [];
  const first = points[0]?.ts ?? 0;
  const span = Math.max(1, (points[points.length - 1]?.ts ?? 1) - first);
  const coords = (key: "memory" | "disk" | "load") =>
    points
      .map(
        (p) =>
          `${(((p.ts - first) / span) * 600).toFixed(1)},${(120 - seriesValue(p, key) * 1.2).toFixed(1)}`,
      )
      .join(" ");
  return (
    <Panel
      title={t("server.history")}
      actions={
        <div className="row">
          {SERIES.map((s) => (
            <span key={s.key} className="faint" style={{ color: s.color }}>
              ■ {s.label}
            </span>
          ))}
        </div>
      }
    >
      {points.length < 2 ? (
        <div className="empty">{t("server.historyEmpty")}</div>
      ) : (
        <>
          <svg className="sparkline" viewBox="0 0 600 120" preserveAspectRatio="none">
            {[30, 60, 90].map((y) => (
              <line key={y} className="grid-line" x1="0" x2="600" y1={y} y2={y} />
            ))}
            {SERIES.map((s) => (
              <polyline key={s.key} points={coords(s.key)} style={{ stroke: s.color }} />
            ))}
          </svg>
          <div className="spread faint">
            <span>{formatDateTime(first)}</span>
            <span>{formatDateTime(points[points.length - 1]?.ts)}</span>
          </div>
        </>
      )}
    </Panel>
  );
}

export function AttackMapPanel() {
  const { t } = useTranslation();
  const attackers = useApi<{
    geoip: boolean;
    sources: { ip: string; count: number; country: string | null }[];
  }>("/server/attackers", 300000);
  const sources = attackers.data?.sources ?? [];
  const heat: Record<string, number> = {};
  for (const s of sources) {
    const code = toMapCode(s.country);
    if (code) heat[code] = (heat[code] ?? 0) + s.count;
  }
  return (
    <Panel title={t("server.attackMap")}>
      {!attackers.data?.geoip && <div className="dim">{t("server.geoMissing")}</div>}
      <WorldMap focus={null} heat={heat} />
      <div className="row">
        {sources.length === 0 && <span className="faint">{t("server.noAttacks")}</span>}
        {sources.map((s) => (
          <Chip key={s.ip} tone={s.country === "LAN" ? "warn" : "crit"}>
            {countryName(s.country)} · {s.ip} · {s.count}×
          </Chip>
        ))}
      </div>
      <div className="faint" style={{ fontSize: "var(--fs-xs)" }}>
        IP Geolocation by DB-IP (db-ip.com)
      </div>
    </Panel>
  );
}

export function CertPanel() {
  const { t } = useTranslation();
  const certs = useApi<{
    targets: string[];
    results: {
      target: string;
      subject: string;
      days_left: number | null;
      error: string | null;
    }[];
    last_check: number | null;
  }>("/security/certs");

  async function check() {
    certs.setData(await api.post("/security/certs/check"));
  }

  return (
    <Panel
      title={t("server.certs")}
      actions={
        <button className="btn small" onClick={() => void check()}>
          {t("server.checkNow")}
        </button>
      }
    >
      {(certs.data?.targets ?? []).length === 0 && (
        <div className="dim">{t("server.certsEmpty")}</div>
      )}
      <div className="list">
        {(certs.data?.results ?? []).map((r) => {
          const tone = r.error
            ? "muted"
            : (r.days_left ?? 0) < 0
              ? "crit"
              : (r.days_left ?? 0) < 30
                ? "warn"
                : "ok";
          return (
            <div className="list-item" key={r.target}>
              <span
                className={`stripe ${tone === "crit" ? "crit" : tone === "warn" ? "warn" : ""}`}
              />
              <div>
                <span className="mono">{r.target}</span> <span className="dim">{r.subject}</span>
              </div>
              <Chip tone={tone}>
                {r.error ?? t("server.daysLeft", { days: Math.floor(r.days_left ?? 0) })}
              </Chip>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
