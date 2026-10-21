"use client";

/** Signed, ranked SHAP attributions in the same units as the credit score. */

import type { Explainability } from "@/lib/types";

export function ShapFactorList({
  explainability,
  limit = 7,
}: {
  explainability: Explainability;
  limit?: number;
}) {
  const factors = [...explainability.all_factors]
    .sort((a, b) => Math.abs(b.score_impact_points) - Math.abs(a.score_impact_points))
    .slice(0, limit);
  const maxImpact = Math.max(1, ...factors.map((factor) => Math.abs(factor.score_impact_points)));

  return (
    <div>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Why this score
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
            SHAP attributions vs. the portfolio average, converted to score points.
          </p>
        </div>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
          {explainability.score_impact_total_points >= 0 ? "+" : ""}
          {explainability.score_impact_total_points} pts net
        </span>
      </div>

      <ul className="mt-3 space-y-2.5">
        {factors.map((factor) => {
          const positive = factor.direction === "reduces_risk";
          const width = Math.max(6, (Math.abs(factor.score_impact_points) / maxImpact) * 100);
          return (
            <li key={factor.feature}>
              <div className="flex items-baseline justify-between gap-2 text-[11px]">
                <span className="font-medium text-slate-700">{factor.label}</span>
                <span className={positive ? "text-emerald-700" : "text-rose-700"}>
                  {factor.value ?? "—"}
                  <span className="ml-1.5 font-semibold tabular-nums">
                    {factor.score_impact_points >= 0 ? "+" : ""}
                    {factor.score_impact_points}
                  </span>
                </span>
              </div>
              <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-slate-100">
                <span
                  className={`h-full rounded-full ${positive ? "bg-emerald-500" : "bg-rose-500"}`}
                  style={{ width: `${width}%` }}
                />
              </div>
              {factor.narrative && (
                <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-slate-500">
                  {factor.narrative}
                </p>
              )}
            </li>
          );
        })}
      </ul>

      {explainability.summary && (
        <p className="mt-3 rounded-lg bg-slate-50 p-2.5 text-[10px] leading-relaxed text-slate-600">
          {explainability.summary}
        </p>
      )}
    </div>
  );
}
