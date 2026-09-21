import { Suspense } from "react";
import { notFound } from "next/navigation";

import AtlasShell from "@/components/AtlasShell";
import { LOCALES, t } from "@/lib/i18n";
import type { Locale } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AtlasPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!(LOCALES as string[]).includes(locale)) notFound();
  const active = locale as Locale;
  return (
    <Suspense
      fallback={
        <div className="app">
          <div className="map-wrap" style={{ background: "var(--ink-900)" }} />
          <div className="statusbar">
            <span className="status-pill">{t(active, "ui.loading")}</span>
          </div>
        </div>
      }
    >
      <AtlasShell locale={active} />
    </Suspense>
  );
}
