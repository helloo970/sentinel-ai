import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { Alert, PortRow, ProcessRow, ResponseMatch } from "../api/types";
import { type DefenseTip, DefenseFlow } from "../components/DefenseFlow";
import { Chip, Panel } from "../components/Panel";
import { runAction, useApi } from "../hooks/useApi";
import { refreshStatus } from "../hooks/useEvents";
import { formatAgo, formatDateTime, severityTone } from "../lib/format";
import { pushToast, store, useStore } from "../state/store";

type Tab =
  "alerts" | "pc" | "tests" | "processes" | "ports" | "autostart" | "sysmon" | "quarantine";
const TABS: Tab[] = [
  "alerts",
  "pc",
  "tests",
  "processes",
  "ports",
  "autostart",
  "sysmon",
  "quarantine",
];

interface AlertTest {
  rule: string;
  group: string;
  severity: "info" | "warn" | "critical";
  title: string;
  protects: string;
  defense: string[];
  tips: DefenseTip[];
}

function AlertTests() {
  const { t } = useTranslation();
  const tests = useApi<AlertTest[]>("/security/tests");
  const [done, setDone] = useState<Record<string, boolean>>({});
  const [current, setCurrent] = useState<{ test: AlertTest; runId: number } | null>(null);
  const groups = new Map<string, AlertTest[]>();
  for (const test of tests.data ?? []) {
    groups.set(test.group, [...(groups.get(test.group) ?? []), test]);
  }

  async function run(test: AlertTest) {
    try {
      await api.post("/security/tests", { rule: test.rule });
      setDone((d) => ({ ...d, [test.rule]: true }));
      setCurrent({ test, runId: Date.now() });
    } catch (error) {
      pushToast({ title: t("security.tests"), message: String(error), severity: "warn" });
    }
  }

  async function runAll() {
    for (const test of tests.data ?? []) {
      await run(test);
      await new Promise((resolve) => setTimeout(resolve, 4500));
    }
  }

  return (
    <div className="grid g2" style={{ alignItems: "start" }}>
      <Panel
        title={t("security.tests")}
        actions={
          <button className="btn small" onClick={() => void runAll()}>
            {t("security.testAll")}
          </button>
        }
      >
        <div className="dim">{t("security.testHint")}</div>
        {[...groups.entries()].map(([group, items]) => (
          <div className="stack" key={group}>
            <span className="label">{group}</span>
            <div className="list">
              {items.map((test) => (
                <div className="list-item" key={test.rule}>
                  <span className={`stripe ${severityTone(test.severity)}`} />
                  <div className="stack" style={{ gap: 2 }}>
                    <span>{test.protects}</span>
                    <span className="faint">{test.title}</span>
                  </div>
                  <div className="row">
                    {done[test.rule] && <Chip tone="ok">{t("security.testSent")}</Chip>}
                    <button className="btn small" onClick={() => void run(test)}>
                      {t("security.testRun")}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </Panel>
      <div style={{ position: "sticky", top: 0 }}>
        {current ? (
          <DefenseFlow
            title={current.test.title}
            steps={current.test.defense}
            tips={current.test.tips}
            runId={current.runId}
          />
        ) : (
          <Panel title={t("defense.title")}>
            <div className="dim">{t("defense.empty")}</div>
          </Panel>
        )}
      </div>
    </div>
  );
}

interface CapabilityApp {
  app: string;
  last_start: number | null;
  in_use: boolean;
}

interface PcSecurity {
  usb: { id: string; name: string; vendor: string; keyboard: boolean }[];
  drives: { mount: string; fs: string }[];
  extensions: { browser: string; profile: string; id: string; name: string; broad: string[] }[];
  camera: CapabilityApp[];
  microphone: CapabilityApp[];
  feed: { hashes: number; last: number | null; error: string | null; enabled: boolean };
}

interface FileScan {
  id: number;
  ts: number;
  path: string;
  verdict: "clean" | "malicious";
  defender: string;
}

function CapabilityList({ title, apps }: { title: string; apps: CapabilityApp[] }) {
  const { t } = useTranslation();
  return (
    <Panel title={title}>
      <div className="list">
        {apps.length === 0 && <div className="empty">{t("common.empty")}</div>}
        {apps.slice(0, 8).map((a) => (
          <div className="list-item" key={a.app}>
            <span className={`stripe ${a.in_use ? "warn" : ""}`} />
            <div className="mono" style={{ wordBreak: "break-all" }}>
              {a.app}
            </div>
            {a.in_use ? (
              <Chip tone="warn" dot>
                {t("security.inUse")}
              </Chip>
            ) : (
              <span className="faint">
                {t("security.lastUsed")} {formatAgo(a.last_start)}
              </span>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

function PcSecurityTab() {
  const { t } = useTranslation();
  const data = useApi<PcSecurity>("/security/pc", 30000);
  const scans = useApi<FileScan[]>("/security/scans", 30000);
  const [path, setPath] = useState("");
  const [updating, setUpdating] = useState(false);
  const pc = data.data;

  async function scanFile() {
    if (!path.trim()) return;
    await runAction("scan_file", { path: path.trim() });
    await scans.reload();
  }

  async function updateFeed() {
    setUpdating(true);
    try {
      await api.post("/security/feed/update");
      await data.reload();
    } finally {
      setUpdating(false);
    }
  }

  return (
    <div className="stack">
      <div className="grid g2">
        <Panel
          title={t("security.scans")}
          actions={
            pc && (
              <>
                <Chip tone={pc.feed.error || !pc.feed.enabled ? "warn" : "ok"}>
                  {pc.feed.enabled
                    ? t("security.feedHashes", { count: pc.feed.hashes })
                    : t("security.feedOff")}
                </Chip>
                <button
                  className="btn small"
                  disabled={updating || !pc.feed.enabled}
                  onClick={() => void updateFeed()}
                >
                  {t("security.feedUpdate")}
                </button>
              </>
            )
          }
        >
          <div className="row">
            <input
              className="input"
              placeholder={t("security.scanPath")}
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void scanFile()}
            />
            <button className="btn small primary" onClick={() => void scanFile()}>
              {t("security.scanFile")}
            </button>
          </div>
          {pc?.feed.error && <div className="warn">{pc.feed.error}</div>}
          <div className="list">
            {(scans.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
            {(scans.data ?? []).slice(0, 12).map((s) => (
              <div className="list-item" key={s.id}>
                <span className={`stripe ${s.verdict === "malicious" ? "crit" : "ok"}`} />
                <div className="stack" style={{ gap: 2 }}>
                  <span className="mono" style={{ wordBreak: "break-all" }}>
                    {s.path}
                  </span>
                  <span className="faint">
                    {formatDateTime(s.ts)} · Defender: {s.defender}
                  </span>
                </div>
                <Chip tone={s.verdict === "malicious" ? "crit" : "ok"}>
                  {t(`security.${s.verdict}`)}
                </Chip>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title={t("security.usb")} actions={pc && <Chip tone="ok">{pc.usb.length}</Chip>}>
          <div className="list" style={{ maxHeight: 360, overflowY: "auto" }}>
            {(pc?.usb ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
            {(pc?.usb ?? []).map((d) => (
              <div className="list-item" key={d.id}>
                <span className={`stripe ${d.keyboard ? "warn" : ""}`} />
                <div className="stack" style={{ gap: 2 }}>
                  <span>{d.name}</span>
                  <span className="faint mono">{d.vendor || d.id}</span>
                </div>
                {d.keyboard && <Chip tone="warn">{t("security.keyboard")}</Chip>}
              </div>
            ))}
          </div>
          {(pc?.drives ?? []).length > 0 && (
            <div className="row" style={{ marginTop: 8 }}>
              <span className="dim">{t("security.drives")}:</span>
              {pc?.drives.map((d) => (
                <Chip key={d.mount} tone="info">
                  {d.mount} {d.fs}
                </Chip>
              ))}
            </div>
          )}
        </Panel>
      </div>
      <div className="grid g2">
        <CapabilityList title={t("security.camera")} apps={pc?.camera ?? []} />
        <CapabilityList title={t("security.microphone")} apps={pc?.microphone ?? []} />
      </div>
      <Panel title={t("security.extensions")}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Browser</th>
                <th>{t("security.broad")}</th>
              </tr>
            </thead>
            <tbody>
              {(pc?.extensions ?? []).map((e) => (
                <tr key={`${e.browser}-${e.profile}-${e.id}`}>
                  <td>{e.name}</td>
                  <td className="dim">
                    {e.browser} · {e.profile}
                  </td>
                  <td>
                    <div className="row">
                      {e.broad.map((p) => (
                        <Chip key={p} tone="warn">
                          {p}
                        </Chip>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(pc?.extensions ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
        </div>
      </Panel>
    </div>
  );
}

function DeviceDecision({ alert, onDone }: { alert: Alert; onDone: () => void }) {
  const { t } = useTranslation();
  const mac = String(alert.detail.mac ?? "");
  const ip = String(alert.detail.ip ?? "");
  async function known() {
    const label = window.prompt(t("network.label"), String(alert.detail.hostname ?? ""));
    if (label === null) return;
    const result = await runAction("mark_device", { mac, label: label || null, trusted: true });
    if (result?.status === "executed") onDone();
  }
  async function unknown() {
    pushToast(
      { title: t("security.unknownDevice"), message: t("security.unknownHint"), severity: "warn" },
      12000,
    );
    await runAction("port_check", { ip });
  }
  return (
    <div className="row">
      <button className="btn small primary" onClick={() => void known()}>
        {t("security.knownDevice")}
      </button>
      <button className="btn small danger" onClick={() => void unknown()}>
        {t("security.unknownDevice")}
      </button>
    </div>
  );
}

function ResponseButtons({ alertId }: { alertId: number }) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<ResponseMatch[] | null>(null);
  if (options === null) {
    return (
      <button
        className="btn small"
        onClick={async () =>
          setOptions(await api.get<ResponseMatch[]>(`/security/alerts/${alertId}/responses`))
        }
      >
        {t("response.defend")}
      </button>
    );
  }
  if (options.length === 0) return <span className="faint">{t("response.noMatch")}</span>;
  return (
    <div className="row">
      {options.map((o) => (
        <button
          key={o.action}
          className={`btn small ${o.risk_level === "high" || o.risk_level === "critical" ? "danger" : ""}`}
          title={o.description}
          onClick={() => void runAction(o.action, { alert_id: alertId })}
        >
          {o.name}
          {o.requires_pin ? " · PIN" : ""}
        </button>
      ))}
    </div>
  );
}

function Alerts() {
  const { t } = useTranslation();
  const version = useStore((s) => s.alertsVersion);
  const [filter, setFilter] = useState<"new" | "all">("new");
  const alerts = useApi<Alert[]>(`/security/alerts?status=${filter}&limit=200`, 0, [version]);
  const [open, setOpen] = useState<number | null>(null);

  async function ack(id?: number) {
    await api.post(id ? `/security/alerts/${id}/ack` : "/security/alerts/ack-all");
    await Promise.all([alerts.reload(), refreshStatus()]);
  }

  return (
    <Panel
      title={t("security.alerts")}
      actions={
        <>
          <select
            className="select"
            value={filter}
            onChange={(e) => setFilter(e.target.value as "new" | "all")}
            style={{ width: "auto" }}
          >
            <option value="new">new</option>
            <option value="all">all</option>
          </select>
          <button className="btn small" onClick={() => void ack()}>
            {t("security.ackAll")}
          </button>
        </>
      }
    >
      <div className="list">
        {(alerts.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
        {(alerts.data ?? []).map((a) => (
          <div className="list-item" key={a.id}>
            <span className={`stripe ${severityTone(a.severity)}`} />
            <div className="stack" style={{ gap: 4 }}>
              <button
                className="btn ghost small"
                style={{
                  justifyContent: "flex-start",
                  textTransform: "none",
                  letterSpacing: 0,
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--fs-md)",
                  clipPath: "none",
                  border: 0,
                  padding: 0,
                  background: "none",
                }}
                onClick={() => setOpen(open === a.id ? null : a.id)}
              >
                {a.title}
              </button>
              <span className="faint mono">
                {a.rule} · {formatDateTime(a.ts)} · {formatAgo(a.ts)}
              </span>
              {a.response && (
                <span className="dim" style={{ fontSize: "var(--fs-sm)" }}>
                  {t("security.response")}: {a.response.action} → {a.response.status} (
                  {a.response.message})
                </span>
              )}
              {open === a.id && <pre className="code">{JSON.stringify(a.detail, null, 2)}</pre>}
              {open === a.id && <ResponseButtons alertId={a.id} />}
              {a.rule === "new_device" && a.status === "new" && (
                <DeviceDecision alert={a} onDone={() => void ack(a.id)} />
              )}
            </div>
            <div className="row">
              <Chip tone={severityTone(a.severity)}>{a.severity}</Chip>
              {a.status === "new" && (
                <button className="btn small ghost" onClick={() => void ack(a.id)}>
                  {t("security.ack")}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Processes() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const procs = useApi<ProcessRow[]>(
    `/security/processes?limit=150${query ? `&q=${encodeURIComponent(query)}` : ""}`,
    10000,
    [query],
  );
  return (
    <Panel
      title={t("security.processes")}
      actions={
        <input
          className="input"
          placeholder={t("common.search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: 220 }}
        />
      }
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>PID</th>
              <th>Name</th>
              <th>RAM</th>
              <th>User</th>
              <th>Path</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(procs.data ?? []).map((p) => (
              <tr key={p.pid}>
                <td className="mono">{p.pid}</td>
                <td>{p.name}</td>
                <td className="num">{p.memory_mb} MB</td>
                <td className="dim">{p.username.split("\\").pop()}</td>
                <td className="mono faint">{p.exe}</td>
                <td>
                  <button
                    className="btn small danger"
                    onClick={() => void runAction("kill_process", { pid: p.pid, reason: "manual" })}
                  >
                    {t("security.kill")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Ports() {
  const ports = useApi<PortRow[]>("/security/ports", 15000);
  return (
    <Panel title="Listening ports">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Port</th>
              <th>Address</th>
              <th>Process</th>
              <th>PID</th>
              <th>Reachable</th>
            </tr>
          </thead>
          <tbody>
            {(ports.data ?? []).map((p, i) => (
              <tr key={`${p.address}-${p.port}-${i}`}>
                <td className="mono">{p.port}</td>
                <td className="mono">{p.address}</td>
                <td>{p.process}</td>
                <td className="mono">{p.pid}</td>
                <td>
                  {p.public ? <Chip tone="warn">network</Chip> : <Chip tone="ok">local</Chip>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Autostart() {
  const entries =
    useApi<{ location: string; name: string; command: string }[]>("/security/autostart");
  return (
    <Panel title="Autostart">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Command</th>
              <th>Location</th>
            </tr>
          </thead>
          <tbody>
            {(entries.data ?? []).map((e, i) => (
              <tr key={i}>
                <td>{e.name}</td>
                <td className="mono">{e.command}</td>
                <td className="faint mono">{e.location}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Sysmon() {
  const data = useApi<{
    status: string;
    events: { record: number; event: string; time: string; data: Record<string, string> }[];
  }>("/security/sysmon?limit=80", 30000);
  // The backend reports "active" (older versions sent "aktiv")
  const active = data.data?.status === "active" || data.data?.status === "aktiv";
  return (
    <Panel
      title="Sysmon"
      actions={
        <Chip tone={active ? "ok" : "warn"} dot>
          {data.data?.status ?? "…"}
        </Chip>
      }
    >
      {!active && (
        <div className="dim">
          Set up Sysmon: run <span className="mono">scripts\setup-sysmon.ps1</span> once as
          administrator.
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Event</th>
              <th>Time</th>
              <th>Image</th>
            </tr>
          </thead>
          <tbody>
            {(data.data?.events ?? [])
              .slice()
              .reverse()
              .map((e) => (
                <tr key={e.record}>
                  <td className="mono">{e.record}</td>
                  <td>{e.event}</td>
                  <td className="mono">{e.time.slice(11, 19)}</td>
                  <td className="mono faint">
                    {e.data.Image ?? e.data.TargetFilename ?? e.data.QueryName ?? ""}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Quarantine() {
  const { t } = useTranslation();
  const items =
    useApi<{ id: number; original_path: string; sha256: string; ts: number; restored: number }[]>(
      "/security/quarantine",
    );
  return (
    <Panel title={t("security.quarantine")}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>SHA-256</th>
              <th>Time</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(items.data ?? []).map((q) => (
              <tr key={q.id}>
                <td className="mono">{q.original_path}</td>
                <td className="mono faint">{q.sha256.slice(0, 16)}…</td>
                <td>{formatDateTime(q.ts)}</td>
                <td>
                  {q.restored ? (
                    <Chip tone="muted">restored</Chip>
                  ) : (
                    <button
                      className="btn small"
                      onClick={async () => {
                        await runAction("restore_quarantine", { id: q.id });
                        void items.reload();
                      }}
                    >
                      {t("security.restore")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(items.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
      </div>
    </Panel>
  );
}

export function Security() {
  const { t } = useTranslation();
  const requestedTab = useStore((s) => s.viewTab);
  const [tab, setTab] = useState<Tab>("alerts");
  useEffect(() => {
    if (requestedTab && (TABS as string[]).includes(requestedTab)) {
      setTab(requestedTab as Tab);
      store.set({ viewTab: null });
    }
  }, [requestedTab]);
  const halted = useStore((s) => s.status?.halted);

  async function scan() {
    try {
      const result = await api.post<{ findings: number }>("/security/scan");
      pushToast({ title: "Scan", message: `${result.findings} findings`, severity: "info" });
    } catch (error) {
      pushToast({ title: "Scan", message: String(error), severity: "warn" });
    }
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("security.title")}</h1>
        <button className="btn" disabled={halted} onClick={() => void scan()}>
          {t("dashboard.scan")}
        </button>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            className="tab"
            aria-selected={tab === name}
            onClick={() => setTab(name)}
          >
            {t(`security.${name}`)}
          </button>
        ))}
      </div>
      {tab === "alerts" && <Alerts />}
      {tab === "pc" && <PcSecurityTab />}
      {tab === "tests" && <AlertTests />}
      {tab === "processes" && <Processes />}
      {tab === "ports" && <Ports />}
      {tab === "autostart" && <Autostart />}
      {tab === "sysmon" && <Sysmon />}
      {tab === "quarantine" && <Quarantine />}
    </div>
  );
}
