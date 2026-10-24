import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { ActionOutcome } from "../api/types";
import { remainingSeconds } from "../lib/format";
import { dismissToast, pushToast, type Toast, useStore } from "../state/store";

function UndoButton({ toast }: { toast: Toast }) {
  const { t } = useTranslation();
  const [left, setLeft] = useState(() => remainingSeconds(toast.expires));
  useEffect(() => {
    const timer = setInterval(() => setLeft(remainingSeconds(toast.expires)), 500);
    return () => clearInterval(timer);
  }, [toast.expires]);
  if (!toast.undoId || left <= 0) return null;

  async function undo() {
    try {
      const result = await api.post<ActionOutcome>(`/undo/${toast.undoId}`);
      pushToast({ title: t("common.undo"), message: result.message, severity: "info" });
    } catch (error) {
      pushToast({ title: t("common.undo"), message: String(error), severity: "warn" });
    }
    dismissToast(toast.id);
  }

  const total = toast.expires && toast.created ? toast.expires - toast.created / 1000 : 60;
  return (
    <>
      <div className="spread">
        <button className="btn small" onClick={() => void undo()}>
          {t("common.undo")} ({left} s)
        </button>
      </div>
      <div className="countdown" style={{ transform: `scaleX(${left / Math.max(1, total)})` }} />
    </>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className="toast" data-severity={toast.severity}>
          <div className="spread">
            <span className="title">{toast.title}</span>
            <button
              className="btn small ghost"
              aria-label="close"
              onClick={() => dismissToast(toast.id)}
            >
              ×
            </button>
          </div>
          <span>{toast.message}</span>
          <UndoButton toast={toast} />
        </div>
      ))}
    </div>
  );
}
