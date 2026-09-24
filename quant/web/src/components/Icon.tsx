/**
 * A small inline-SVG icon set (1.6px strokes, 24-unit grid) so the bundle needs no icon font.
 *   <Icon name="search" size={16} />
 */
import type { CSSProperties } from "react";

const PATHS = {
  markets: "M3 20h18M6 16l4-5 3 3 5-7 M18 7h-3 M18 7v3",
  portfolio: "M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z M9 8V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V8 M4 13h16",
  optimize: "M4 19c3-9 7-13 16-14 M4 19h16 M4 19V5 M9 12.5a1 1 0 1 0 0 .01 M14 8.5a1 1 0 1 0 0 .01",
  research: "M9 3h6 M10 3v6l-5 9a1.5 1.5 0 0 0 1.3 2.2h11.4A1.5 1.5 0 0 0 19 18l-5-9V3 M7.5 14h9",
  options: "M3 17c3 0 4-10 9-10s6 10 9 10 M3 21h18",
  macro: "M4 18c2.5-.5 4-3 6-6s4-5.5 10-6 M4 21h16 M4 3v18",
  company: "M4 21V6l8-3 8 3v15 M9 21v-4h6v4 M8 9h1 M15 9h1 M8 13h1 M15 13h1 M3 21h18",
  ticker: "M4 20V10 M9 20V4 M14 20v-7 M19 20V8",
  engine: "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2.2 2.2 M16.2 16.2l2.2 2.2 M5.6 18.4l2.2-2.2 M16.2 7.8l2.2-2.2 M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6",
  book: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14 M20 20l-4-4",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8 M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  menu: "M4 7h16 M4 12h16 M4 17h16",
  sidebar: "M4 4h16v16H4z M9.5 4v16",
  "chevron-left": "M15 6l-6 6 6 6",
  "chevron-right": "M9 6l6 6-6 6",
  "chevron-down": "M6 9l6 6 6-6",
  "chevron-up": "M6 15l6-6 6 6",
  x: "M6 6l12 12 M18 6L6 18",
  plus: "M12 5v14 M5 12h14",
  minus: "M5 12h14",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18 M12 11v5 M12 7.5v.5",
  help: "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18 M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.4 M12 17v.4",
  alert: "M12 4l9 16H3z M12 10v4 M12 17v.4",
  "cloud-off": "M3 3l18 18 M8 7.5A6 6 0 0 1 17.5 11H18a3.5 3.5 0 0 1 2.3 6.1 M16 19H7a4 4 0 0 1-1.6-7.7",
  "arrow-up-right": "M7 17L17 7 M8 7h9v9",
  "arrow-right": "M5 12h14 M13 6l6 6-6 6",
  share: "M12 3v12 M7 8l5-5 5 5 M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5",
  upload: "M12 15V3 M7 8l5-5 5 5 M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
  trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
  sort: "M8 4v16 M4 8l4-4 4 4 M16 20V4 M12 16l4 4 4-4",
  check: "M5 12.5l4.5 4.5L19 7",
  external: "M14 4h6v6 M20 4l-9 9 M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  database: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3 M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  scale: "M12 3v18 M5 21h14 M6 7h12 M6 7l-3 7a3 3 0 0 0 6 0z M18 7l-3 7a3 3 0 0 0 6 0z",
  copy: "M9 9h11v11H9z M5 15H4V4h11v1",
  command: "M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7",
  grid: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
  list: "M9 6h11 M9 12h11 M9 18h11 M4.5 6v.01 M4.5 12v.01 M4.5 18v.01",
  candles: "M7 3v3 M7 16v5 M5 6h4v10H5z M17 3v6 M17 17v4 M15 9h4v8h-4z",
  line: "M3 17l5-6 4 3 8-9",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, strokeWidth = 1.6, className, style, title }: { name: IconName; size?: number; strokeWidth?: number; className?: string; style?: CSSProperties; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flex: "none", ...style }}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}
