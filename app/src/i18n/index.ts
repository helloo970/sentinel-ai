import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";

export const resources = { en: { translation: en } } as const;

/** The UI is English only. */
export function initI18n() {
  if (!i18n.isInitialized) {
    void i18n.use(initReactI18next).init({
      resources,
      lng: "en",
      fallbackLng: "en",
      interpolation: { escapeValue: false },
    });
  }
  return i18n;
}

export default i18n;
