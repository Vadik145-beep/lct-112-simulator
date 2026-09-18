import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import type { ChartOption } from "@/components/chart-theme";

echarts.use([BarChart, HeatmapChart, LineChart, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

/** One ECharts instance bound to a div; the option is rebuilt by the caller for the
 *  palette, so a theme switch redraws the chart in the new colours. */
export function Chart({ option, height = 280, label, className }: { option: ChartOption; height?: number; label: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    chart.current = echarts.init(el, undefined, { renderer: "canvas" });
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={ref} role="img" aria-label={label} className={className} style={{ width: "100%", height }} />;
}
