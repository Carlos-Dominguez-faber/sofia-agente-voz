"use client";

/**
 * The panel's front door.
 *
 * Deliberately says nothing about what is behind it beyond the clinic's own
 * name — no hint of which systems are connected, no version, no service state.
 * Someone who lands here without the password should learn nothing from the
 * page itself.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { branding } from "@/config/branding";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const payload = await response.json().catch(() => null);

    if (response.ok && payload?.ok) {
      router.replace(params.get("next") || "/");
      router.refresh();
    } else {
      setError(payload?.message ?? "No pude iniciar sesión.");
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="text-center">
        <span aria-hidden className="text-3xl">
          {branding.logoMark}
        </span>
        <h1 className="mt-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
          {branding.clinicName}
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">{branding.tagline}</p>
      </div>

      <div>
        <label
          htmlFor="password"
          className="block text-sm font-medium text-slate-700 dark:text-slate-300"
        >
          Contraseña
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={!password || busy}
        className="w-full rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-40"
      >
        {busy ? "Entrando…" : "Entrar"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4 dark:bg-slate-950">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
