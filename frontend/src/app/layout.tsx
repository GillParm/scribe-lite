import type { Metadata } from "next";
import { Atkinson_Hyperlegible_Next, Source_Serif_4 } from "next/font/google";
import Link from "next/link";

import "./globals.css";

const atkinson = Atkinson_Hyperlegible_Next({
  subsets: ["latin"],
  variable: "--font-atkinson",
  display: "swap",
});
const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  variable: "--font-source-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "scribe-lite", template: "%s | scribe-lite" },
  description:
    "Demo AI medical scribe: a recorded conversation becomes a SOAP note linked to its transcript. Not for clinical use.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${atkinson.variable} ${sourceSerif.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <div
          role="note"
          aria-label="Demo notice"
          className="sticky top-0 z-30 bg-ink px-4 py-2 text-center text-sm text-white"
        >
          <strong className="font-semibold">Demo only. Not for clinical use.</strong> Synthetic
          conversations only; never upload a real patient recording.
        </div>
        <header className="border-b border-rule bg-paper">
          <nav className="mx-auto flex max-w-[1400px] flex-wrap items-baseline gap-x-8 gap-y-2 px-6 py-4">
            <Link href="/" className="text-xl font-bold tracking-tight">
              scribe-lite
            </Link>
            <Link href="/" className="text-ink-soft hover:text-ink">
              Encounters
            </Link>
            <Link href="/audit" className="text-ink-soft hover:text-ink">
              Audit log
            </Link>
            <a
              href="https://github.com/GillParm/scribe-lite"
              className="ml-auto text-sm text-ink-soft hover:text-ink"
            >
              Source on GitHub
            </a>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
