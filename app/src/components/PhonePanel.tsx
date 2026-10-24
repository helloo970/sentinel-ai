import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { useApi } from "../hooks/useApi";
import { pushToast } from "../state/store";
import { Chip, Panel } from "./Panel";

/** Pair a phone: show the code only once, never store it (the PC keeps only derived keys). */
export function PhonePanel() {
  const { t } = useTranslation();
  const status = useApi<{ paired: boolean; url: string | null }>("/phone/status", 30000);
  const [pin, setPin] = useState("");
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function pair() {
    setBusy(true);
    try {
      const result = await api.post<{ code: string }>("/phone/pair", { pin });
      setCode(result.code);
      await status.reload();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ title: t("phone.title"), message, severity: "warn" });
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  async function unpair() {
    if (!window.confirm(t("phone.confirmUnpair"))) return;
    await api.post("/phone/unpair");
    setCode(null);
    await status.reload();
  }

  const groups = code?.match(/.{1,4}/g) ?? [];
  return (
    <Panel
      title={t("phone.title")}
      actions={
        <Chip tone={status.data?.paired ? "ok" : "muted"} dot>
          {status.data?.paired ? t("phone.paired") : t("phone.notPaired")}
        </Chip>
      }
    >
      <div className="dim">{t("phone.explain")}</div>
      {status.data?.url && (
        <div>
          {t("phone.open")} <span className="mono">{status.data.url}</span>
        </div>
      )}
      {code ? (
        <div className="stack">
          <div className="mono" style={{ fontSize: "var(--fs-xl)", letterSpacing: "0.12em" }}>
            {groups.join(" ")}
          </div>
          <div className="warn">{t("phone.codeOnce")}</div>
          <button className="btn small" onClick={() => setCode(null)}>
            {t("phone.done")}
          </button>
        </div>
      ) : (
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void pair();
          }}
        >
          <input
            className="input"
            type="password"
            inputMode="numeric"
            value={pin}
            placeholder={t("phone.ownerPin")}
            onChange={(e) => setPin(e.target.value)}
            style={{ width: 180 }}
          />
          <button className="btn small primary" disabled={busy || pin.length < 4}>
            {status.data?.paired ? t("phone.repair") : t("phone.pair")}
          </button>
          {status.data?.paired && (
            <button type="button" className="btn small danger" onClick={() => void unpair()}>
              {t("phone.unpair")}
            </button>
          )}
        </form>
      )}
      <div className="faint" style={{ fontSize: "var(--fs-sm)" }}>
        {t("phone.security")}
      </div>
    </Panel>
  );
}
