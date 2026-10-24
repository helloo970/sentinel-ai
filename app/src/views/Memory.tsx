import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import type { Memory as MemoryItem } from "../api/types";
import { Chip, Panel } from "../components/Panel";
import { useApi } from "../hooks/useApi";
import { formatDateTime } from "../lib/format";
import { pushToast } from "../state/store";

const CATEGORIES = ["note", "person", "preference", "work", "place", "task"];

export function Memory() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const path = query.trim() ? `/memory/search?q=${encodeURIComponent(query.trim())}` : "/memory";
  const memories = useApi<MemoryItem[]>(path, 0, [query]);
  const [text, setText] = useState("");
  const [category, setCategory] = useState("note");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);

  async function guard(fn: () => Promise<unknown>) {
    try {
      await fn();
      await memories.reload();
    } catch (error) {
      pushToast({
        title: t("memory.title"),
        message: error instanceof ApiError ? error.message : String(error),
        severity: "warn",
      });
    }
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>{t("memory.title")}</h1>
        <button
          className="btn danger small"
          onClick={() =>
            window.confirm(t("memory.confirmDeleteAll")) && void guard(() => api.del("/memory"))
          }
        >
          {t("memory.deleteAll")}
        </button>
      </div>
      <p className="dim">{t("memory.hint")}</p>
      <Panel title={t("memory.add")}>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void guard(async () => {
              await api.post("/memory", { text, category });
              setText("");
            });
          }}
        >
          <input
            className="input grow"
            value={text}
            maxLength={500}
            onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Prefers short answers"
          />
          <select
            className="select"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            style={{ width: "auto" }}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button className="btn primary" disabled={!text.trim()}>
            {t("common.add")}
          </button>
        </form>
      </Panel>
      <Panel
        title={`${memories.data?.length ?? 0} memories`}
        actions={
          <input
            className="input"
            placeholder={t("common.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: 240 }}
          />
        }
      >
        <div className="list">
          {(memories.data ?? []).length === 0 && <div className="empty">{t("common.empty")}</div>}
          {(memories.data ?? []).map((m) => (
            <div className="list-item" key={m.id}>
              <span className="stripe" />
              {editing?.id === m.id ? (
                <form
                  className="row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void guard(async () => {
                      await api.put(`/memory/${m.id}`, {
                        text: editing.text,
                        category: m.category,
                      });
                      setEditing(null);
                    });
                  }}
                >
                  <input
                    className="input grow"
                    value={editing.text}
                    maxLength={500}
                    onChange={(e) => setEditing({ id: m.id, text: e.target.value })}
                    autoFocus
                  />
                  <button className="btn small primary">{t("common.save")}</button>
                  <button
                    type="button"
                    className="btn small ghost"
                    onClick={() => setEditing(null)}
                  >
                    {t("common.cancel")}
                  </button>
                </form>
              ) : (
                <div>
                  <div>{m.text}</div>
                  <div className="faint mono">
                    {t("memory.source")}: {m.source} · {formatDateTime(m.updated)}
                    {m.score !== undefined ? ` · score ${m.score}` : ""}
                  </div>
                </div>
              )}
              <div className="row">
                <Chip tone="muted">{m.category}</Chip>
                <button
                  className="btn small ghost"
                  onClick={() => setEditing({ id: m.id, text: m.text })}
                >
                  {t("common.edit")}
                </button>
                <button
                  className="btn small danger"
                  onClick={() => void guard(() => api.del(`/memory/${m.id}`))}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
