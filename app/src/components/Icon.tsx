const PATHS: Record<string, string> = {
  overview: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  dashboard: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10z",
  jarvis: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10z",
  security: "M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5z",
  network:
    "M12 4a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM5 16a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm14 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM12 10v3M12 13l-6 4M12 13l6 4",
  automation:
    "M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1",
  agents:
    "M8 7a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm8 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM2 21c0-3.3 2.7-6 6-6s6 2.7 6 6M14 15.3c.6-.2 1.3-.3 2-.3 3.3 0 6 2.7 6 6",
  memory: "M6 3h12v18l-6-4-6 4z",
  audit: "M5 3h14v18H5zM8 8h8M8 12h8M8 16h5",
  settings:
    "M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM12 2l2 3 3.5-.5.5 3.5 3 2-2 3 .5 3.5-3.5.5-2 3-2-3-3.5-.5.5-3.5-3-2 3-2-.5-3.5L10 5z",
  world:
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2c2.8 3 4 6.4 4 10s-1.2 7-4 10c-2.8-3-4-6.4-4-10s1.2-7 4-10z",
  shop: "M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6.2M10 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm8 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  power: "M12 2v10M6.3 5.3a8 8 0 1 0 11.4 0",
  mic: "M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v4",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  send: "M3 11l18-8-8 18-2-8z",
  play: "M7 4l13 8-13 8z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
};

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS.dashboard} />
    </svg>
  );
}
