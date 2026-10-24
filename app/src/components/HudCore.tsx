import { useTranslation } from "react-i18next";
import type { CoreMood, CoreState } from "../lib/coreState";

const TICKS = Array.from({ length: 72 }, (_, i) => i * 5);

/** Animated core of rotating SVG rings; reacts to listening, speaking and alarm. */
export function HudCore({
  state,
  mood = "calm",
  label = true,
}: {
  state: CoreState;
  mood?: CoreMood;
  label?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="core"
      data-state={state}
      data-mood={mood}
      role="img"
      aria-label={t(`core.${state}`)}
    >
      <svg viewBox="0 0 400 400">
        <g className="ticks">
          {TICKS.map((deg) => (
            <line
              key={deg}
              x1="200"
              y1="12"
              x2="200"
              y2={deg % 30 === 0 ? 26 : 20}
              transform={`rotate(${deg} 200 200)`}
            />
          ))}
        </g>
        <circle className="ring r1" cx="200" cy="200" r="176" />
        <circle className="ring r2" cx="200" cy="200" r="150" />
        <circle className="ring r3" cx="200" cy="200" r="132" />
        <circle className="ring r4" cx="200" cy="200" r="114" />
        <circle className="ring r5" cx="200" cy="200" r="96" />
        <circle className="ring r6" cx="200" cy="200" r="78" />
        <circle className="wave w1" cx="200" cy="200" r="170" />
        <circle className="wave w2" cx="200" cy="200" r="170" />
        <circle className="nucleus" cx="200" cy="200" r="56" />
        <circle className="nucleus-core" cx="200" cy="200" r="26" />
        <path
          className="ring"
          d="M200 152 L241.6 176 L241.6 224 L200 248 L158.4 224 L158.4 176 Z"
          strokeWidth="1"
          opacity="0.5"
        />
      </svg>
      {label && <div className="core-label">{t(`core.${state}`)}</div>}
    </div>
  );
}
