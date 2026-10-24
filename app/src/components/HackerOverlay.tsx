import { useEffect, useState } from "react";
import { store, useStore } from "../state/store";

const CHAR_MS = 18;
const LINE_PAUSE_MS = 140;
const HOLD_MS = 700;

/** Short movie-hacker-style terminal window when Jarvis opens a section. */
export function HackerOverlay() {
  const hacker = useStore((s) => s.hacker);
  const [text, setText] = useState("");

  useEffect(() => {
    if (!hacker) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const full = hacker.lines.join("\n");
    if (reduced) {
      setText(full);
      const id = window.setTimeout(() => store.set({ hacker: null }), 900);
      return () => window.clearTimeout(id);
    }
    let index = 0;
    let timer = 0;
    setText("");
    const step = () => {
      index += 1;
      setText(full.slice(0, index));
      if (index >= full.length) {
        timer = window.setTimeout(() => store.set({ hacker: null }), HOLD_MS);
        return;
      }
      timer = window.setTimeout(step, full[index - 1] === "\n" ? LINE_PAUSE_MS : CHAR_MS);
    };
    timer = window.setTimeout(step, CHAR_MS);
    return () => window.clearTimeout(timer);
  }, [hacker]);

  if (!hacker) return null;
  return (
    <div className="hacker-overlay" role="status" onClick={() => store.set({ hacker: null })}>
      <div className="hacker-window">
        <div className="hacker-bar">
          <span />
          <span />
          <span />
          <b>JARVIS://{hacker.title.toUpperCase()}</b>
        </div>
        <pre className="hacker-text">
          {text}
          <span className="hacker-cursor">█</span>
        </pre>
      </div>
    </div>
  );
}
