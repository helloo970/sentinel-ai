import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { Hologram } from "../components/Hologram";
import { Chip } from "../components/Panel";
import { VoiceControl } from "../components/VoiceControl";
import { deriveCoreState } from "../lib/coreState";
import { useStore } from "../state/store";

interface Message {
  role: "user" | "assistant";
  content: string;
  tainted?: boolean;
}

/** Jarvis is controlled by voice only (microphone, "Hey Jarvis", clapping). */
export function Jarvis() {
  const { t } = useTranslation();
  const status = useStore((s) => s.status);
  const voiceState = useStore((s) => s.voiceState);
  const voiceChat = useStore((s) => s.voiceChat);
  const [messages, setMessages] = useState<Message[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  // Reload the history as soon as a voice reply is done
  useEffect(() => {
    api
      .get<{ role: "user" | "assistant"; content: string; tainted: number }[]>(
        "/chat/history?conversation=voice",
      )
      .then((rows) =>
        setMessages(rows.map((r) => ({ role: r.role, content: r.content, tainted: !!r.tainted }))),
      )
      .catch(() => undefined);
  }, [voiceChat?.done]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages]);

  async function newChat() {
    await api.del("/chat/history?conversation=voice");
    setMessages([]);
  }

  const busy = voiceState === "thinking";
  const recent = messages.slice(-6);
  return (
    <div className="jarvis-home">
      <div className="jarvis-core">
        <Hologram state={deriveCoreState(status, voiceState, busy)} />
      </div>
      <div className="jarvis-hint faint">{t("jarvis.homeHint")}</div>
      <div className="jarvis-talk">
        <div className="chat-log" ref={logRef}>
          {recent.length === 0 && <div className="empty">{t("jarvis.voiceEmpty")}</div>}
          {recent.map((m, index) => (
            <div key={index} className={`msg ${m.role}`}>
              <span className="who">
                {m.role === "user" ? status?.user_name || "You" : "JARVIS"}
              </span>
              <span>{m.content}</span>
              {m.tainted && (
                <div className="events">
                  <Chip tone="warn">{t("jarvis.tainted")}</Chip>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <VoiceControl />
          <button className="btn ghost small" onClick={() => void newChat()}>
            {t("jarvis.newChat")}
          </button>
        </div>
      </div>
    </div>
  );
}
