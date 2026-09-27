"use client";
import * as React from "react";
import type {
  AnalysisOut,
  AnomalyResult,
  ClusterResult,
  ConfidenceInterval,
  CorrelationResult,
  ForecastResult,
  ImportanceResult,
  QuadrantResult,
  RegressionResult,
  RFMResult,
  TestResult,
} from "@/lib/api/resources/analysis";
import { ForecastView } from "./forecast-panel";
import { AnomalyView } from "./anomaly-panel";
import { SegmentsView } from "./segments-panel";
import { StatsView } from "./stats-panel";
import { CorrelationView, RegressionView } from "./correlation-panel";
import { useMetricOptions } from "./sources";

/** Renders any stored analysis (from `GET /analysis/{id}`) with the view for its kind. */
export function AnalysisView({ out }: { out: AnalysisOut }) {
  const { metrics } = useMetricOptions();
  const p = out.params as { metric_id?: string; grain?: string; filters?: { dimension: string; op: string; values: unknown[] }[] };
  const format = metrics.find((m) => m.id === p.metric_id)?.format ?? null;
  switch (out.kind) {
    case "forecast":
      return <ForecastView out={out as AnalysisOut<ForecastResult>} format={format} />;
    case "anomalies":
      return (
        <AnomalyView
          out={out as AnalysisOut<AnomalyResult>}
          format={format}
          metricId={p.metric_id ?? ""}
          grain={p.grain ?? "day"}
          filters={p.filters ?? []}
        />
      );
    case "segments":
      return <SegmentsView out={out as AnalysisOut<RFMResult | QuadrantResult | ClusterResult>} />;
    case "stats_test":
      return <StatsView out={out as AnalysisOut<TestResult | ConfidenceInterval>} />;
    case "correlation":
      return <CorrelationView out={out as AnalysisOut<CorrelationResult>} />;
    case "regression":
      return <RegressionView out={out as AnalysisOut<RegressionResult | ImportanceResult>} />;
    default:
      return <p className="text-sm text-fg-muted">Unknown analysis kind: {out.kind}</p>;
  }
}
