import { api } from "../api/client";
import { store } from "../state/store";
import { speakAsync } from "./speech";

/** Jarvis speaks: via the backend voice, otherwise via the browser voice. */
export async function narrate(text: string): Promise<void> {
  if (!text.trim()) return;
  if (store.get().status?.voice.capabilities.tts) {
    const result = await api.post<{ spoken: boolean }>("/voice/say", { text }).catch(() => null);
    if (result?.spoken) return;
  }
  await speakAsync(text);
}

/** True if Jarvis just opened this view by voice (a manual click stays silent). */
export function openedByVoice(view: string, withinMs = 5000): boolean {
  const opened = store.get().voiceView;
  return opened?.view === view && Date.now() - opened.at < withinMs;
}
