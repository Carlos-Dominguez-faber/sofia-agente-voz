/**
 * The patients pipeline, stage by stage.
 *
 * Bars are scaled against the largest stage rather than the total, so a small
 * stage stays visible instead of collapsing to a sliver next to a big one.
 */

import { branding } from "@/config/branding";
import { count } from "@/lib/format";
import type { Funnel as FunnelData } from "@/lib/types";

export function Funnel({ funnel }: { funnel: FunnelData }) {
  const largest = Math.max(...funnel.stages.map((stage) => stage.count), 1);

  return (
    <div className="space-y-2">
      {funnel.stages.map((stage) => (
        <div key={stage.stage_id} className="flex items-center gap-3">
          <span className="w-40 shrink-0 truncate text-sm text-slate-600 dark:text-slate-300">
            {stage.label}
          </span>
          <div className="h-6 flex-1 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
            <div
              className="h-full rounded transition-[width] duration-500"
              style={{
                width: `${Math.max((stage.count / largest) * 100, stage.count > 0 ? 3 : 0)}%`,
                backgroundColor: branding.colors.accent,
              }}
            />
          </div>
          <span className="w-10 shrink-0 text-right text-sm font-medium tabular-nums text-slate-900 dark:text-slate-100">
            {count(stage.count)}
          </span>
        </div>
      ))}

      <p className="pt-1 text-xs text-slate-400 dark:text-slate-500">
        {count(funnel.total)} oportunidades en el pipeline.
        {funnel.unmapped > 0 ? (
          /* Cards in a stage the config does not know about. Said out loud
             because otherwise the numbers simply would not add up. */
          <span className="ml-1 text-amber-600 dark:text-amber-400">
            {count(funnel.unmapped)} en etapas que el panel no reconoce — el pipeline cambió en
            el CRM.
          </span>
        ) : null}
      </p>
    </div>
  );
}
