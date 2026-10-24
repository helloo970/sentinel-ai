import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import type { ResponsePlugin } from "../api/types";
import { useApi } from "../hooks/useApi";
import { askPin } from "../lib/pin";
import { pushToast } from "../state/store";
import { Chip, Panel } from "./Panel";

const STATUS_TONE: Record<string, "ok" | "warn" | "crit" | "muted"> = {
  loaded: "ok",
  pending_approval: "warn",
  disabled: "muted",
  error: "crit",
};

function PinSetup({ isSet, onDone }: { isSet: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const [newPin, setNewPin] = useState("");
  const [oldPin, setOldPin] = useState("");

  async function save() {
    try {
      await api.post("/pin", { new_pin: newPin, old_pin: isSet ? oldPin : null });
      pushToast({ title: "PIN", message: t("response.pinSaved"), severity: "info" });
      setNewPin("");
      setOldPin("");
      onDone();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ title: "PIN", message, severity: "warn" });
    }
  }

  const digits = (value: string) => value.replace(/\D/g, "").slice(0, 12);
  return (
    <form
      className="row"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      {isSet && (
        <input
          className="input"
          style={{ width: 150 }}
          type="password"
          inputMode="numeric"
          autoComplete="off"
          placeholder={t("response.oldPin")}
          value={oldPin}
          onChange={(e) => setOldPin(digits(e.target.value))}
        />
      )}
      <input
        className="input"
        style={{ width: 150 }}
        type="password"
        inputMode="numeric"
        autoComplete="off"
        placeholder={t("response.newPin")}
        value={newPin}
        onChange={(e) => setNewPin(digits(e.target.value))}
      />
      <button className="btn" disabled={newPin.length < 6}>
        {isSet ? t("response.changePin") : t("response.setPin")}
      </button>
    </form>
  );
}

export function ResponsePanel() {
  const { t } = useTranslation();
  const plugins = useApi<ResponsePlugin[]>("/response/plugins");
  const pin = useApi<{ set: boolean; locked_until: number | null }>("/pin");

  async function reload() {
    plugins.setData(await api.post<ResponsePlugin[]>("/response/plugins/reload"));
  }

  async function approve(plugin: ResponsePlugin) {
    const value = await askPin(t("response.approvePrompt", { file: plugin.file }));
    if (!value) return;
    try {
      plugins.setData(
        await api.post<ResponsePlugin[]>("/response/plugins/approve", {
          file: plugin.file,
          sha256: plugin.sha256,
          pin: value,
        }),
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ title: t("response.title"), message, severity: "warn" });
    }
  }

  async function revoke(plugin: ResponsePlugin) {
    plugins.setData(
      await api.post<ResponsePlugin[]>("/response/plugins/revoke", { file: plugin.file }),
    );
  }

  return (
    <Panel
      title={t("response.title")}
      actions={
        <button className="btn small" onClick={() => void reload()}>
          {t("response.reload")}
        </button>
      }
    >
      <p className="faint" style={{ fontSize: "var(--fs-sm)" }}>
        {t("response.hint")}
      </p>
      <div className="row">
        <Chip tone={pin.data?.set ? "ok" : "warn"} dot>
          {pin.data?.set ? t("response.pinActive") : t("response.pinMissing")}
        </Chip>
        {pin.data?.locked_until && <Chip tone="crit">{t("response.pinLocked")}</Chip>}
      </div>
      <PinSetup isSet={Boolean(pin.data?.set)} onDone={() => void pin.reload()} />
      <div className="list">
        {(plugins.data ?? []).map((p) => (
          <div className="list-item" key={`${p.origin}-${p.file}`}>
            <span
              className={`stripe ${p.status === "error" ? "crit" : p.status === "pending_approval" ? "warn" : ""}`}
            />
            <div>
              <strong>{p.name}</strong>{" "}
              <span className="faint mono">
                {p.origin === "builtin" ? t("response.builtin") : p.file}
                {p.risk_level ? ` · ${p.risk_level}` : ""}
                {p.requires_pin ? " · PIN" : ""}
              </span>
              <div className="dim">{p.error ?? p.description}</div>
              {p.origin === "user" && p.sha256 && (
                <div className="faint mono">SHA-256 {p.sha256.slice(0, 16)}…</div>
              )}
            </div>
            <div className="row">
              <Chip tone={STATUS_TONE[p.status] ?? "muted"}>
                {t(`response.status.${p.status}`)}
              </Chip>
              {p.origin === "user" && p.status === "pending_approval" && (
                <button
                  className="btn small primary"
                  disabled={!pin.data?.set}
                  onClick={() => void approve(p)}
                >
                  {t("common.approve")}
                </button>
              )}
              {p.origin === "user" && p.status === "loaded" && (
                <button className="btn small ghost" onClick={() => void revoke(p)}>
                  {t("response.revoke")}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
