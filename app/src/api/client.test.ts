import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, request, setConfig } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
  setConfig(null);
});

describe("api client", () => {
  it("refuses to call without configuration", async () => {
    await expect(request("/status")).rejects.toBeInstanceOf(ApiError);
  });

  it("sends the session token as bearer header", async () => {
    setConfig({ baseUrl: "http://127.0.0.1:8765", token: "secret-token" });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await api.get("/status");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8765/status");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer secret-token");
  });

  it("posts actions with params only (no approval shortcut)", async () => {
    setConfig({ baseUrl: "http://127.0.0.1:8765", token: "t" });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ status: "pending" }), { status: 200 }));
    await api.action("kill_process", { pid: 1234 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8765/actions/kill_process");
    expect(JSON.parse(String(init.body))).toEqual({ params: { pid: 1234 } });
  });

  it("encodes action names", async () => {
    setConfig({ baseUrl: "http://127.0.0.1:8765", token: "t" });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));
    await api.action("../control/resume");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8765/actions/..%2Fcontrol%2Fresume",
    );
  });

  it("raises ApiError with backend detail", async () => {
    setConfig({ baseUrl: "http://127.0.0.1:8765", token: "t" });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Emergency stop active" }), { status: 423 }),
    );
    await expect(api.post("/security/scan")).rejects.toMatchObject({
      status: 423,
      message: "Emergency stop active",
    });
  });
});
