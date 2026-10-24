import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Panel } from "./Panel";

export interface DefenseTip {
  text: string;
  source: string;
  url: string;
}

const PHASES = ["detect", "check", "respond", "secured"] as const;
const STEP_MS = 900;

/** Plays the defense flow step by step: detected → checked → response → secured. */
export function DefenseFlow({
  title,
  steps,
  tips,
  runId,
}: {
  title: string;
  steps: string[];
  tips: DefenseTip[];
  runId: number;
}) {
  const { t } = useTranslation();
  const [shown, setShown] = useState(0);

  useEffect(() => {
    setShown(0);
    const timers = steps.map((_, i) => window.setTimeout(() => setShown(i + 1), (i + 1) * STEP_MS));
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [runId, steps]);

  const finished = shown >= steps.length;
  return (
    <Panel title={t("defense.title")} tone={finished ? undefined : "warn"}>
      <div className="mono" style={{ fontSize: "var(--fs-sm)" }}>
        {title}
      </div>
      <ol className="defense-flow">
        {steps.map((step, i) => {
          const phase = PHASES[Math.min(i, PHASES.length - 1)];
          const state = i < shown ? "done" : i === shown ? "active" : "waiting";
          return (
            <li key={`${runId}-${i}`} className={`defense-step ${state} ${phase}`}>
              <span className="defense-dot" />
              <div className="stack" style={{ gap: 2 }}>
                <span className="label">{t(`defense.${phase}`)}</span>
                <span>{step}</span>
                {phase === "respond" && i < shown && (
                  <span className="faint">{t("defense.simulated")}</span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {finished && <div className="ok">{t("defense.done")}</div>}
      {tips.length > 0 && (
        <div className="stack">
          <span className="label">{t("defense.tips")}</span>
          <ul className="defense-tips">
            {tips.map((tip) => (
              <li key={tip.text}>
                {tip.text} <span className="faint">({tip.source})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}
