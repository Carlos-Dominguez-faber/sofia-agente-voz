/**
 * Saving the prompt, proxied.
 *
 * WHY THIS IS AN EXPLICIT ROUTE AND NOT A CATCH-ALL `[...path]` PROXY:
 *
 * A wildcard proxy that attaches the bearer token forwards whatever path the
 * browser asks for. That would hand any logged-in tab — or any script running
 * in one — the ability to reach every endpoint on the backend, including the
 * action endpoints Retell calls mid-conversation: `/create-lead`,
 * `/book-appointment`, `/update-lead-status`. A panel meant only to read would
 * have become a way to write appointments into a real calendar.
 *
 * So each operation the panel is allowed to perform gets its own handler. What
 * is not written here cannot be reached, and adding a capability is a visible
 * decision rather than an accident of routing.
 */

import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/api";

export async function PUT(request: Request) {
  let editable = "";
  try {
    const body = (await request.json()) as { editable?: unknown };
    editable = typeof body.editable === "string" ? body.editable : "";
  } catch {
    return NextResponse.json(
      { ok: false, message: "No pude leer el prompt enviado." },
      { status: 400 },
    );
  }

  const result = await backendFetch<Record<string, unknown>>("/dashboard/agent/prompt", {
    method: "PUT",
    body: { editable },
  });

  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data, message: "Prompt guardado." });
  }

  // The guardrail refusal arrives here as `unavailable` with the backend's own
  // explanation. That message is written for the person editing, so it is
  // passed through unchanged rather than replaced with something generic.
  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 422 },
  );
}
