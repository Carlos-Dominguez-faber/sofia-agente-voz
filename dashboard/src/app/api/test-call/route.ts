/**
 * "Llámame para probar" — call the owner's own number with the current config.
 *
 * Like the manual call, this reaches a real phone, so it has its own explicit
 * handler and the backend re-validates the number.
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
      { ok: false, message: "Escribe tu número para la prueba." },
      { status: 400 },
    );
  }

  const result = await backendFetch<Record<string, unknown>>("/dashboard/test-call", {
    method: "POST",
    body: { phone },
  });

  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data, message: "Te estamos llamando." });
  }

  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 502 },
  );
}
