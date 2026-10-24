import { api, requestBlob } from "../api/client";
import { store } from "../state/store";

/**
 * Speech output: first the online voice from the backend (Microsoft neural voice),
 * otherwise the system's Web Speech as a fallback.
 */
let current: HTMLAudioElement | null = null;
// Voice volume for the hologram's mouth
let audioCtx: AudioContext | null = null;
let analyser: AnalyserNode | null = null;
const levelBuf = new Uint8Array(512);

function attachAnalyser(audio: HTMLAudioElement): void {
  try {
    audioCtx ??= new AudioContext();
    if (!analyser) {
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.connect(audioCtx.destination);
    }
    audioCtx.createMediaElementSource(audio).connect(analyser);
    void audioCtx.resume();
  } catch {
    // without Web Audio the voice still plays
  }
}

/** Current volume of the online voice (0 … 1), 0 when nothing is playing. */
export function speechLevel(): number {
  if (!analyser || !current) return 0;
  analyser.getByteTimeDomainData(levelBuf);
  let sum = 0;
  for (const v of levelBuf) sum += ((v - 128) / 128) ** 2;
  return Math.min(1, Math.sqrt(sum / levelBuf.length) * 4);
}
const prefetched = new Map<string, Promise<Blob | null>>();

function fetchVoice(text: string): Promise<Blob | null> {
  const key = text.slice(0, 1500);
  let pending = prefetched.get(key);
  if (!pending) {
    pending = requestBlob("/voice/tts", { text: key }).catch(() => null);
    prefetched.set(key, pending);
    if (prefetched.size > 12) prefetched.delete(prefetched.keys().next().value as string);
  }
  return pending;
}

/** Preload the next sentence while the current one still plays (smooth world briefing). */
export function prefetchSpeech(text: string): void {
  if (text.trim() && onlineVoiceActive()) void fetchVoice(text);
}

function onlineVoiceActive(): boolean {
  return store.get().status?.voice.tts_engine === "edge";
}

async function playOnline(text: string): Promise<boolean> {
  try {
    const blob = await fetchVoice(text);
    prefetched.delete(text.slice(0, 1500));
    if (!blob || !blob.size) return false;
    stopSpeaking();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    current = audio;
    attachAnalyser(audio);
    return await new Promise<boolean>((resolve) => {
      const done = (ok: boolean) => {
        URL.revokeObjectURL(url);
        if (current === audio) current = null;
        store.set({ voiceState: "idle" });
        void api.post("/voice/speaking", { on: false }).catch(() => undefined);
        resolve(ok);
      };
      audio.onplay = () => {
        store.set({ voiceState: "speaking" });
        // The microphone must not pick up Jarvis' own voice as a command
        void api.post("/voice/speaking", { on: true }).catch(() => undefined);
      };
      audio.onended = () => done(true);
      audio.onerror = () => done(false);
      // If the browser blocks playback, the system voice takes over
      audio.play().catch(() => done(false));
    });
  } catch {
    return false;
  }
}

function webSpeech(text: string, lang: string, rate: number): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window) || !text.trim()) {
      resolve();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text.slice(0, 1500));
    utterance.lang = lang;
    utterance.rate = rate;
    const voice = window.speechSynthesis
      .getVoices()
      .find((v) => v.lang.startsWith(lang.slice(0, 2)));
    if (voice) utterance.voice = voice;
    const timer = setTimeout(resolve, 2000 + text.length * 90);
    const done = () => {
      clearTimeout(timer);
      store.set({ voiceState: "idle" });
      resolve();
    };
    utterance.onstart = () => store.set({ voiceState: "speaking" });
    utterance.onend = done;
    utterance.onerror = done;
    window.speechSynthesis.speak(utterance);
  });
}

/** Speaks and waits until the end. */
export async function speakAsync(text: string, lang = "en-US"): Promise<void> {
  if (!text.trim()) return;
  if (onlineVoiceActive() && (await playOnline(text))) return;
  await webSpeech(text, lang, 1.0);
}

/** Speaks in the background (cancels a running announcement). */
export function speak(text: string, lang = "en-US"): void {
  stopSpeaking();
  void speakAsync(text, lang);
}

export function stopSpeaking(): void {
  if (current) {
    current.pause();
    current = null;
  }
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
}
