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
