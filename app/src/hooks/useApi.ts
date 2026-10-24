import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ActionOutcome } from "../api/types";
import { pushToast } from "../state/store";
import { refreshApprovals } from "./useEvents";

/** Loads data from the API, optionally on an interval and whenever `deps` change. */
export function useApi<T>(path: string | null, intervalMs = 0, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await api.get<T>(path));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    void load();
    if (!intervalMs) return;
    const timer = setInterval(() => void load(), intervalMs);
    return () => clearInterval(timer);
  }, [load, intervalMs]);

  return { data, error, loading, reload: load, setData };
}

/** Runs an action as a direct UI command and reports the result. */
export async function runAction(
  name: string,
  params: Record<string, unknown> = {},
): Promise<ActionOutcome | null> {
  try {
    const result = await api.action(name, params);
    if (result.status === "pending") {
      pushToast({ title: "Confirmation needed", message: result.message, severity: "warn" });
      await refreshApprovals();
    } else if (result.status !== "executed") {
      pushToast({ title: "Denied", message: result.message, severity: "warn" }, 9000);
    }
    return result;
  } catch (error) {
    pushToast({ title: "Error", message: String(error), severity: "critical" });
    return null;
  }
}
