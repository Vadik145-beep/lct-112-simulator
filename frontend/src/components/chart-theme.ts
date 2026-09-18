import { useEffect, useState } from "react";

import type { EChartsCoreOption } from "echarts/core";

export type ChartOption = EChartsCoreOption;

/** Colours of the current theme read from the CSS variables so charts follow the toggle. */
export interface ChartPalette {
  text: string;
  muted: string;
  border: string;
  card: string;
  primary: string;
  success: string;
  warning: string;
  danger: string;
  series: string[];
}

let probe: CanvasRenderingContext2D | null | undefined;

/** A CSS variable resolved to a hex colour: the theme uses oklch(), which the chart
 *  library cannot parse or interpolate, so the browser paints one pixel and we read it. */
function cssVar(name: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (probe === undefined) {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    probe = canvas.getContext("2d", { willReadFrequently: true });
  }
  if (!probe || !raw) return raw;
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = raw;
  probe.fillRect(0, 0, 1, 1);
  const [r = 0, g = 0, b = 0, a = 0] = probe.getImageData(0, 0, 1, 1).data;
  if (a === 0) return raw;
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export function readPalette(): ChartPalette {
  const primary = cssVar("--primary");
  const success = cssVar("--success");
  const warning = cssVar("--warning");
  const danger = cssVar("--destructive");
  return {
    text: cssVar("--foreground"),
    muted: cssVar("--muted-foreground"),
    border: cssVar("--border"),
    card: cssVar("--card"),
    primary,
    success,
    warning,
    danger,
    series: [primary, "#3b82c4", success, warning, danger],
  };
}

/** The palette, refreshed when the theme class on <html> changes. */
export function usePalette(): ChartPalette {
  const [palette, setPalette] = useState<ChartPalette>(() => readPalette());
  useEffect(() => {
    const observer = new MutationObserver(() => setPalette(readPalette()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return palette;
}

/** Axis and text defaults shared by the charts of the cabinet. */
export function baseOption(p: ChartPalette): ChartOption {
  return {
    textStyle: { color: p.text, fontFamily: "inherit" },
    grid: { left: 48, right: 16, top: 32, bottom: 40, containLabel: false },
    tooltip: {
      backgroundColor: p.card,
      borderColor: p.border,
      textStyle: { color: p.text },
    },
  };
}
