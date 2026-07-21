/**
 * One call in full — fetched when a row in the table is expanded.
 *
 * Loading transcripts for every row up front would mean one backend request per
 * row on every page view, to render text nobody has asked to read yet.
 */

import { NextResponse } from "next/server";

import { getCallDetail } from "@/lib/api";

export async function GET(_request: Request, context: { params: Promise<{ callId: string }> }) {
  const { callId } = await context.params;

  const result = await getCallDetail(callId);
  if (result.status === "ok") {
    return NextResponse.json({ ok: true, data: result.data });
  }

  return NextResponse.json(
    { ok: false, message: result.message, detail: result.detail },
    { status: 503 },
  );
}
