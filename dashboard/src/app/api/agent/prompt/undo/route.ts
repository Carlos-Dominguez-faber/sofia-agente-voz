/** Restore the prompt that was live before the last save. */

import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/api";

export async function POST() {
  const result = await backendFetch<Record<string, unknown>>("/dashboard/agent/prompt/undo", {
    method: "POST",
  });

  if (result.status === "ok") {
    return NextResponse.json({
      ok: true,
      data: result.data,
      message: "Restauré la versión anterior.",
    });
  }

  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 502 },
  );
}
