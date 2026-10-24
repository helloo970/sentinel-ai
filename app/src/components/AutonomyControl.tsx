import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { refreshStatus } from "../hooks/useEvents";
import { pushToast, useStore } from "../state/store";

export function AutonomyControl({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const level = useStore((s) => s.status?.autonomy ?? 2);

  async function set(next: number) {
    if (next === 3 && !window.confirm(t("autonomy.desc3"))) return;
    try {
      await api.put("/control/autonomy", { level: next });
    } catch (error) {
      pushToast({ title: t("autonomy.label"), message: String(error), severity: "warn" });
    }
    await refreshStatus();
  }

  return (
    <div className="autonomy" role="group" aria-label={t("autonomy.label")}>
      {[0, 1, 2, 3].map((n) => (
        <button
          key={n}
          aria-pressed={level === n}
          title={t(`autonomy.desc${n}`)}
          onClick={() => void set(n)}
        >
          {compact ? n : `${n} ${t(`autonomy.${n}`)}`}
        </button>
      ))}
    </div>
  );
}
