import { useState } from "react";
import { useTranslation } from "react-i18next";
import { answerPin } from "../lib/pin";
import { useStore } from "../state/store";
import { Panel } from "./Panel";

export function PinDialog() {
  const { t } = useTranslation();
  const request = useStore((s) => s.pinRequest);
  const [pin, setPin] = useState("");
  if (!request) return null;

  function finish(value: string | null) {
    setPin("");
    answerPin(value);
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <Panel title={t("response.pinTitle")} tone="warn">
          <p>{request.title}</p>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              finish(pin);
            }}
          >
            <input
              className="input grow"
              type="password"
              inputMode="numeric"
              autoComplete="off"
              autoFocus
              maxLength={12}
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
              aria-label="PIN"
            />
            <button className="btn primary" disabled={pin.length < 6}>
              {t("common.approve")}
            </button>
            <button type="button" className="btn ghost" onClick={() => finish(null)}>
              {t("common.cancel")}
            </button>
          </form>
        </Panel>
      </div>
    </div>
  );
}
