/** Points of a price line for an SVG (width w, height h). */
export function sparkPoints(series: number[], w: number, h: number, pad = 2): string {
  if (series.length < 2) return "";
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  return series
    .map((v, i) => {
      const x = (i / (series.length - 1)) * w;
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function formatPrice(value: number | null, currency: string): string {
  if (value === null) return "–";
  const digits = value < 10 ? 4 : value < 1000 ? 2 : 0;
  const text = value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const unit: Record<string, string> = { EUR: "€", USD: "$", TRY: "₺", GBP: "£", PTS: "pts" };
  return `${text} ${unit[currency] ?? currency}`.trim();
}
