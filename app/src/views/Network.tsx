import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { Device } from "../api/types";
import { Chip, Panel } from "../components/Panel";
import {
  AttackMapPanel,
  CertPanel,
  HistoryPanel,
  BackupPanel,
  PiholePanel,
  ServerOverview,
  type ServerStatus,
} from "../components/ServerPanels";
import { runAction, useApi } from "../hooks/useApi";
import { formatAgo } from "../lib/format";
import { pushToast, useStore } from "../state/store";

export function Network() {
  const { t } = useTranslation();
  const halted = useStore((s) => s.status?.halted);
  const inventory = useApi<{ subnet: string | null; devices: Device[] }>("/network/devices", 30000);
  const [ports, setPorts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const server = useApi<ServerStatus>("/server/status", 60000);
  const lookups = useApi<{ geoip: boolean; oui: boolean }>("/network/data");

  async function refreshServer() {
    try {
      server.setData(await api.post<ServerStatus>("/server/refresh"));
    } catch (error) {
      pushToast({ title: t("network.server"), message: String(error), severity: "warn" });
    }
  }

  async function refreshArp() {
    setBusy(true);
    try {
      const result = await api.post<{ seen: number; new: number }>("/network/refresh");
      pushToast({
        title: "ARP",
        message: `${result.seen} devices, ${result.new} new`,
        severity: "info",
      });
      await inventory.reload();
    } catch (error) {
      pushToast({ title: "ARP", message: String(error), severity: "warn" });
    } finally {
      setBusy(false);
    }
  }

  async function discover() {
    setBusy(true);
    await runAction("network_discover");
    await inventory.reload();
    setBusy(false);
  }

  async function portCheck(ip: string) {
    setPorts((p) => ({ ...p, [ip]: "…" }));
    const result = await runAction("port_check", { ip });
    const open = (result?.data as { port: number; service: string }[] | null) ?? [];
    setPorts((p) => ({
      ...p,
      [ip]:
        result?.status === "executed"
          ? open.map((o) => `${o.port}/${o.service}`).join(", ") || "none"
          : (result?.message ?? "–"),
    }));
  }

  async function mark(device: Device, trusted: boolean) {
    const label = trusted
      ? window.prompt(t("network.label"), device.label ?? device.hostname ?? "")
      : device.label;
    if (label === null) return;
    await runAction("mark_device", { mac: device.mac, label: label || null, trusted });
    await inventory.reload();
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("network.title")}</h1>
        <div className="row">
          <Chip tone="info">
            {t("network.subnet")}: {inventory.data?.subnet ?? "–"}
          </Chip>
          <button className="btn" disabled={busy || halted} onClick={() => void refreshArp()}>
            {t("network.arp")}
          </button>
          <button className="btn primary" disabled={busy || halted} onClick={() => void discover()}>
            {t("network.discover")}
          </button>
        </div>
      </div>
      <Panel title="Devices">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>IP</th>
                <th>Name</th>
                <th>{t("network.vendor")}</th>
                <th>MAC</th>
                <th>Status</th>
                <th>Last seen</th>
                <th>Ports</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(inventory.data?.devices ?? []).map((d) => (
                <tr key={d.mac}>
                  <td className="mono">{d.ip}</td>
                  <td>{d.label ?? d.hostname ?? <span className="faint">unknown</span>}</td>
                  <td className="dim">{d.vendor ?? "–"}</td>
                  <td className="mono faint">{d.mac}</td>
                  <td>
                    {d.trusted ? <Chip tone="ok">trusted</Chip> : <Chip tone="warn">new</Chip>}
                  </td>
                  <td>{formatAgo(d.last_seen)}</td>
                  <td className="mono">
                    {ports[d.ip] ??
                      (d.ports
                        ? d.ports.map((o) => `${o.port}/${o.service}`).join(", ") || "none"
                        : "")}
                    {d.ports?.some((o) => o.risky) && <Chip tone="warn">risky</Chip>}
                  </td>
                  <td className="row">
                    <button className="btn small" onClick={() => void portCheck(d.ip)}>
                      {t("network.portCheck")}
                    </button>
                    <button className="btn small ghost" onClick={() => void mark(d, !d.trusted)}>
                      {d.trusted ? t("network.untrust") : t("network.trust")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(inventory.data?.devices ?? []).length === 0 && (
            <div className="empty">No devices yet – use "Read ARP" or "Scan home network".</div>
          )}
        </div>
      </Panel>
      {lookups.data && !lookups.data.oui && <div className="faint">{t("network.dataMissing")}</div>}
      <div className="grid g2">
        <ServerOverview server={server.data} onRefresh={() => void refreshServer()} />
        <PiholePanel pihole={server.data?.data?.pihole} />
        <BackupPanel server={server.data} />
      </div>
      <HistoryPanel />
      <div className="grid g2">
        <AttackMapPanel />
        <CertPanel />
      </div>
    </div>
  );
}
