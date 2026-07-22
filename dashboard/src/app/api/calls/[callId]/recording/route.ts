/**
 * Stream a call recording, gated twice.
 *
 * The audio is patient PII. It reaches the browser only through here, which
 * means it passes both gates: the panel session (this route is behind
 * proxy.ts's default-deny) and the backend token (added server-side below). The
 * raw Retell URL never touches the browser — the backend fetches it and streams
 * the bytes, and this route forwards that stream with the token attached.
 *
 * An <audio> element cannot send an Authorization header, which is exactly why
 * the token lives here on the server and not in the page.
 */

import { backendToken, backendUrl } from "@/lib/env";

export async function GET(
  _request: Request,
  context: { params: Promise<{ callId: string }> },
) {
  const { callId } = await context.params;

  let url: string;
  let token: string;
  try {
    url = `${backendUrl()}/dashboard/calls/${encodeURIComponent(callId)}/recording`;
    token = backendToken();
  } catch {
    return new Response("Backend no configurado.", { status: 500 });
  }

  const upstream = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    return new Response("No se pudo cargar la grabación.", {
      status: upstream.status || 502,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "audio/wav",
      "Cache-Control": "private, no-store",
    },
  });
}
