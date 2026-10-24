import countries from "i18n-iso-countries";
import en from "i18n-iso-countries/langs/en.json";

countries.registerLocale(en);

/** ISO alpha-2 (e.g. "RU") → three-digit ISO number used by the world map (e.g. "643"). */
export function toMapCode(alpha2: string | null | undefined): string | null {
  if (!alpha2 || alpha2 === "LAN") return null;
  return countries.alpha2ToNumeric(alpha2.toUpperCase()) ?? null;
}

export function countryName(alpha2: string | null | undefined): string {
  if (!alpha2) return "unknown";
  if (alpha2 === "LAN") return "Home network";
  return countries.getName(alpha2.toUpperCase(), "en", { select: "alias" }) ?? alpha2;
}
