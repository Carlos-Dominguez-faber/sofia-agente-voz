import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { branding } from "@/config/branding";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: `${branding.clinicName} · ${branding.tagline}`,
  description: `Panel de ${branding.agentName}, la recepcionista de ${branding.clinicName}.`,
  // The panel holds patient data. Keeping it out of search engines costs
  // nothing and removes the most common way a private URL stops being private.
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="es-MX"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-slate-50 dark:bg-slate-950">{children}</body>
    </html>
  );
}
