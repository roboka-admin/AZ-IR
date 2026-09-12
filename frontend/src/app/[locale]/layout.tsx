import type { Metadata } from "next";
import type { ReactNode } from "react";

import "../globals.css";
import { DEFAULT_LOCALE, DIRECTION, LOCALES, t } from "@/lib/i18n";
import type { Locale } from "@/lib/types";

/**
 * Root layout for the localized segment. `lang` and `dir` come from the URL, so Persian renders
 * right-to-left without any client-side flip (locked decision: fa primary + RTL, en secondary).
 */

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const active: Locale = (LOCALES as string[]).includes(locale) ? (locale as Locale) : DEFAULT_LOCALE;
  return {
    title: t(active, "app.title"),
    description: t(active, "app.tagline"),
    applicationName: t(active, "app.shortTitle"),
    alternates: {
      languages: Object.fromEntries(LOCALES.map((code) => [code, `/${code}`])),
    },
    other: { "content-language": active },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const active: Locale = (LOCALES as string[]).includes(locale) ? (locale as Locale) : DEFAULT_LOCALE;
  return (
    <html lang={active} dir={DIRECTION[active]}>
      <body>{children}</body>
    </html>
  );
}
