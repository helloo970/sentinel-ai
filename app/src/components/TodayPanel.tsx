import { useTranslation } from "react-i18next";
import { useApi } from "../hooks/useApi";
import { formatTime } from "../lib/format";
import { Chip, Metric, Panel } from "./Panel";

interface HomeWeather {
  configured: boolean;
  error?: string;
  place?: string;
  temperature?: number | null;
  text?: string;
  min?: number | null;
  max?: number | null;
  rain_chance?: number | null;
}

interface UnreadMail {
  configured: boolean;
  error?: string;
  address?: string;
  mails: {
    from_name: string;
    from_address: string;
    subject: string;
    date: number | null;
    important: boolean;
  }[];
}

/** Weather at home and unread Gmail messages (sender and subject only). */
export function TodayPanel() {
  const { t } = useTranslation();
  const weather = useApi<HomeWeather>("/weather/home", 900000);
  const mail = useApi<UnreadMail>("/mail/unread", 300000);
  const w = weather.data;
  const mails = mail.data?.mails ?? [];
  return (
    <Panel
      title={t("today.title")}
      actions={
        mail.data?.configured ? (
          <Chip tone={mails.some((m) => m.important) ? "warn" : "info"}>
            {t("today.unread", { count: mails.length })}
          </Chip>
        ) : null
      }
    >
      <div className="grid g2">
        <div className="stack">
          {!w?.configured && <div className="dim">{t("today.weatherMissing")}</div>}
          {w?.error && <div className="warn">{w.error}</div>}
          {w?.configured && !w.error && (
            <Metric
              label={w.place ?? ""}
              value={
                w.temperature !== null && w.temperature !== undefined
                  ? `${Math.round(w.temperature)}°`
                  : "–"
              }
              sub={`${w.text ?? ""}${
                w.min !== null && w.min !== undefined && w.max !== null && w.max !== undefined
                  ? ` · ${Math.round(w.min)}–${Math.round(w.max)}°`
                  : ""
              }${w.rain_chance ? ` · ${w.rain_chance} % rain` : ""}`}
            />
          )}
        </div>
        <div className="list">
          {!mail.data?.configured && <div className="dim">{t("today.mailMissing")}</div>}
          {mail.data?.error && <div className="warn">{mail.data.error}</div>}
          {mail.data?.configured && mails.length === 0 && !mail.data.error && (
            <div className="empty">{t("today.noMail")}</div>
          )}
          {mails.slice(0, 6).map((m, i) => (
            <div className="list-item" key={`${m.from_address}-${m.date}-${i}`}>
              <span className={`stripe ${m.important ? "warn" : ""}`} />
              <div>
                <div>{m.subject}</div>
                <div className="faint" title={m.from_address}>
                  {m.from_name}
                </div>
              </div>
              <div className="row">
                {m.important && <Chip tone="warn">{t("today.important")}</Chip>}
                <span className="faint mono">{formatTime(m.date)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
