/**
 * The panel — the seven sections, in the order the clinic reads them.
 *
 * A Server Component: every backend call happens here, on the server, with the
 * token that never reaches the browser. Each section is fetched independently
 * and rendered through `WithResult`, so a source that is down costs its own
 * section and nothing else. One dead API never blanks the page.
 */

import { Funnel } from "@/components/Funnel";
import { LeadTemperature } from "@/components/LeadTemperature";
import { ManualCall } from "@/components/ManualCall";
import { MetricCards } from "@/components/MetricCards";
import { PromptEditor } from "@/components/PromptEditor";
import { RecentCalls } from "@/components/RecentCalls";
import { ServicesStatus } from "@/components/ServicesStatus";
import { Section, WithResult } from "@/components/SourceState";
import { branding } from "@/config/branding";
import {
  getCalls,
  getFunnel,
  getMetrics,
  getPrompt,
  getServicesStatus,
  getTemperature,
} from "@/lib/api";

/** Live operational data: never served from a cache. */
export const dynamic = "force-dynamic";

const RANGE_DAYS = 30;

export default async function DashboardPage() {
  // Fetched together rather than in sequence: seven round trips one after
  // another is seven times the wait for a page that is read at a glance.
  const [metrics, temperature, funnel, calls, prompt, services] = await Promise.all([
    getMetrics(RANGE_DAYS),
    getTemperature(),
    getFunnel(),
    getCalls(RANGE_DAYS, 20),
    getPrompt(),
    getServicesStatus(),
  ]);

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span aria-hidden className="text-3xl">
            {branding.logoMark}
          </span>
          <div>
            <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
              {branding.clinicName}
            </h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">{branding.tagline}</p>
          </div>
        </div>
        <form action="/api/auth/logout" method="post">
          <button
            type="submit"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Salir
          </button>
        </form>
      </header>

      {/* 1 — Métricas */}
      <Section title={`Últimos ${RANGE_DAYS} días`}>
        <WithResult result={metrics}>{(data) => <MetricCards metrics={data} />}</WithResult>
      </Section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* 2 — Temperatura */}
        <Section
          title="Temperatura de pacientes"
          description="Qué tan cerca están de tomar tratamiento"
        >
          <WithResult result={temperature}>
            {(data) => <LeadTemperature temperature={data} />}
          </WithResult>
        </Section>

        {/* 3 — Funnel */}
        <Section title="Pipeline" description="En qué etapa va cada paciente">
          <WithResult result={funnel}>{(data) => <Funnel funnel={data} />}</WithResult>
        </Section>
      </div>

      {/* 4 — Llamadas recientes */}
      <Section title="Llamadas recientes">
        <WithResult result={calls}>{(data) => <RecentCalls list={data} />}</WithResult>
      </Section>

      {/* 5 — El prompt */}
      <Section
        title={`Cómo habla ${branding.agentName}`}
        description="Edita aquí. No hace falta entrar a ningún otro sistema."
      >
        <WithResult result={prompt}>{(data) => <PromptEditor prompt={data} />}</WithResult>
      </Section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* 6 — Llamada manual */}
        <Section title="Llamar a un paciente" description="Marca ahora mismo">
          <ManualCall agentName={branding.agentName} />
        </Section>

        {/* 7 — Estado */}
        <Section title="Estado del sistema">
          <WithResult result={services}>{(data) => <ServicesStatus status={data} />}</WithResult>
        </Section>
      </div>

      <footer className="pt-2 pb-6 text-center text-xs text-slate-400 dark:text-slate-500">
        {branding.supportLine}
      </footer>
    </main>
  );
}
