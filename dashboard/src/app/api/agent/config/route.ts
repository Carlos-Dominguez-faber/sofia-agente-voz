/**
 * Read and change Sofía's voice + behaviour, proxied.
 *
 * Explicit handler, not a wildcard — same reason as every other write route:
 * the browser must never be able to reach the backend's action endpoints, only
 * the operations the panel is meant to perform.
 */

import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/api";
import type { AgentConfig } from "@/lib/types";

export async function GET() {
  const result = await backendFetch<AgentConfig>("/dashboard/agent-config");
  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data });
  }
  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 503 },
  );
}

export async function POST(request: Request) {
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ ok: false, message: "No pude leer el cambio." }, { status: 400 });
  }

  // Only forward the fields this endpoint owns. The backend re-validates every
  // bound regardless — this is a convenience filter, not the security boundary.
  const payload: Record<string, unknown> = {};
  for (const key of ["voice_id", "voice_speed", "expressiveness", "behaviour"]) {
    if (body[key] !== undefined && body[key] !== null) payload[key] = body[key];
  }

  const result = await backendFetch<Record<string, unknown>>("/dashboard/agent-config", {
    method: "POST",
    body: payload,
  });

  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data, message: "Cambios aplicados." });
  }

  // A bounds violation comes back as `unavailable` with the backend's own
  // message; pass it through so the person editing sees exactly what was wrong.
  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 422 },
  );
}
