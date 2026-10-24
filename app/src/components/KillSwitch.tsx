import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { refreshStatus } from "../hooks/useEvents";
import { pushToast, useStore } from "../state/store";
import { Icon } from "./Icon";

export async function triggerHalt(via: "ui" | "hotkey" = "ui"): Promise<void> {
  try {
    await api.post("/control/halt", { via });
  } finally {
    await refreshStatus();
  }
}

/** Emergency stop: always visible. Triggers instantly, releasing asks first. */
export function KillSwitch() {
  const { t } = useTranslation();
  const halted = useStore((s) => s.status?.halted ?? false);

  async function onClick() {
    if (!halted) {
      await triggerHalt("ui");
      return;
    }
    if (!window.confirm(t("killswitch.confirmRelease"))) return;
    try {
      await api.post("/control/resume");
    } catch (error) {
      pushToast({ title: "Error", message: String(error), severity: "critical" });
    }
    await refreshStatus();
  }

  return (
    <button
      className="killswitch"
      data-halted={halted}
      onClick={onClick}
      title={halted ? t("killswitch.release") : "Ctrl+Alt+End"}
    >
      <Icon name="power" size={16} />
      {halted ? t("killswitch.halted") : t("killswitch.stop")}
    </button>
  );
}
