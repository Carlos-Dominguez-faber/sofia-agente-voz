/**
 * Place a manual outbound call.
 *
 * This is the one action in the panel that reaches a real person's phone. It is
 * kept behind its own explicit handler for the same reason as the prompt save,
 * and it is worth stating plainly: a bug here does not corrupt data, it rings
 * somebody's telephone.
 */

import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/api";

export async function POST(request: Request) {
  let phone = "";
  try {
    const body = (await request.json()) as { phone?: unknown };
    phone = typeof body.phone === "string" ? body.phone.trim() : "";
  } catch {
    phone = "";
  }

  if (!phone) {
    return NextResponse.json(
      { ok: false, message: "Escribe un número para llamar." },
      { status: 400 },
    );
  }

  const result = await backendFetch<Record<string, unknown>>("/dashboard/outbound/call", {
    method: "POST",
    body: { phone },
  });

  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data, message: "Llamada iniciada." });
  }

  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 502 },
  );
}
