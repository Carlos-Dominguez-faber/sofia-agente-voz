/**
 * The gate. Runs before every request that is not explicitly public.
 *
 * Without this, the panel's deployment URL is the only thing standing between
 * the internet and a list of patients' call transcripts. A URL is not a secret:
 * it gets shared, pasted into chats and indexed.
 *
 * Default-deny by design — the matcher below lets nothing through except the
 * login page, the login endpoint and Next's own static assets. A new route is
 * protected the moment it exists, without anyone remembering to protect it.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, verifySession } from "@/lib/session";

export async function proxy(request: NextRequest) {
  const secret = process.env.DASHBOARD_SESSION_SECRET;

  if (!secret) {
    // Fail closed. A panel that cannot verify sessions must not serve patient
    // data just because it is misconfigured.
    return new NextResponse(
      "El panel no tiene DASHBOARD_SESSION_SECRET configurado. Revisa las variables de entorno.",
      { status: 500 },
    );
  }

  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (await verifySession(secret, session)) {
    return NextResponse.next();
  }

  // An expired API call gets a status it can act on; a person gets the login
  // page with a way back to where they were going.
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json(
      { ok: false, error: { code: "unauthenticated", detail: "Session missing or expired" } },
      { status: 401 },
    );
  }

  const loginUrl = new URL("/login", request.url);
  if (request.nextUrl.pathname !== "/") {
    loginUrl.searchParams.set("next", request.nextUrl.pathname);
  }
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    /*
     * Everything except:
     *   /login          the page that hands out sessions
     *   /api/auth/*     the endpoints that create and destroy them
     *   /_next/*        build output
     *   favicon, icons  static assets
     */
    "/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)",
  ],
};
