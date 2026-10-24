import { api } from "../api/client";

/** Writes a UI error to the backend log (for debugging). */
export function reportError(where: string, error: unknown): void {
  const message =
    error instanceof Error
      ? `${error.name}: ${error.message}
${error.stack ?? ""}`
      : String(error);
  void api.post("/client/log", { where, message: message.slice(0, 2000) }).catch(() => undefined);
}
