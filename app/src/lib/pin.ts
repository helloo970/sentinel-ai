import { store } from "../state/store";

let resolver: ((pin: string | null) => void) | null = null;

/** Asks for the owner PIN in a dedicated dialog (never in plain text via window.prompt). */
export function askPin(title: string): Promise<string | null> {
  resolver?.(null);
  return new Promise((resolve) => {
    resolver = resolve;
    store.set({ pinRequest: { title } });
  });
}

export function answerPin(pin: string | null): void {
  const resolve = resolver;
  resolver = null;
  store.set({ pinRequest: null });
  resolve?.(pin && /^\d{6,12}$/.test(pin) ? pin : null);
}
