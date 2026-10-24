/** Short fire alarm (smoke detector pattern: 3 beeps, pause), generated with Web Audio. */

let context: AudioContext | null = null;
let playingUntil = 0;

function audio(): AudioContext | null {
  try {
    context ??= new AudioContext();
    if (context.state === "suspended") void context.resume();
    return context;
  } catch {
    return null;
  }
}

/** Unlock on the first click – browsers only play audio after a user action. */
export function unlockSiren(): void {
  audio();
}

function beep(ctx: AudioContext, start: number, length: number, gain: GainNode): void {
  const osc = ctx.createOscillator();
  osc.type = "square";
  osc.frequency.setValueAtTime(950, start);
  osc.frequency.linearRampToValueAtTime(1250, start + length);
  osc.connect(gain);
  osc.start(start);
  osc.stop(start + length);
}

/** critical: 2 rounds (~4 s), warn: 1 round (~1.8 s). */
export function playSiren(severity: "warn" | "critical" = "critical", volume = 0.35): void {
  const ctx = audio();
  if (!ctx) return;
  const now = ctx.currentTime;
  if (now < playingUntil) return; // don't play on top of each other
  const gain = ctx.createGain();
  gain.gain.value = Math.max(0, Math.min(1, volume));
  gain.connect(ctx.destination);
  const rounds = severity === "critical" ? 2 : 1;
  let t = now + 0.05;
  for (let round = 0; round < rounds; round++) {
    for (let i = 0; i < 3; i++) {
      beep(ctx, t, 0.4, gain);
      t += 0.6;
    }
    t += 0.4;
  }
  playingUntil = t;
}
