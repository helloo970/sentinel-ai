import { readSse } from "./sse";
import type { ActionOutcome } from "./types";

export interface BackendConfig {
  baseUrl: string;
  token: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let config: BackendConfig | null = null;

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function setConfig(next: BackendConfig | null): void {
  config = next;
}

export function getConfig(): BackendConfig | null {
  return config;
}

/** Gets port and session token from the Tauri process (never from files in the frontend). */
export async function loadTauriConfig(): Promise<BackendConfig> {
  const { invoke } = await import("@tauri-apps/api/core");
  const result = await invoke<{ port: number; token: string }>("backend_config");
  return { baseUrl: `http://127.0.0.1:${result.port}`, token: result.token };
}

function headers(extra?: HeadersInit): Headers {
  const result = new Headers(extra);
  if (config) result.set("Authorization", `Bearer ${config.token}`);
  return result;
}

export async function request<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  if (!config) throw new ApiError(0, "Backend not connected");
  const response = await fetch(`${config.baseUrl}${path}`, {
    method: init.method ?? "GET",
    headers: headers(init.body !== undefined ? { "Content-Type": "application/json" } : undefined),
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Response without JSON
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

/** Binary response (e.g. MP3 from the online voice). */
export async function requestBlob(path: string, body: unknown): Promise<Blob> {
  if (!config) throw new ApiError(0, "Backend not connected");
  const response = await fetch(`${config.baseUrl}${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return response.blob();
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** Direct UI command. The engine still decides (e.g. confirmation). */
  action: (name: string, params: Record<string, unknown> = {}) =>
    request<ActionOutcome>(`/actions/${encodeURIComponent(name)}`, {
      method: "POST",
      body: { params },
    }),
};

export async function* stream<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): AsyncGenerator<T> {
  if (!config) throw new ApiError(0, "Backend not connected");
  const response = await fetch(`${config.baseUrl}${path}`, {
    method: init.method ?? "GET",
    headers: headers(init.body !== undefined ? { "Content-Type": "application/json" } : undefined),
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });
  if (!response.ok || !response.body) throw new ApiError(response.status, response.statusText);
  for await (const event of readSse(response.body, init.signal)) {
    yield event as T;
  }
}

export async function waitForBackend(timeoutMs = 30000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      await request("/status");
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return false;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}
