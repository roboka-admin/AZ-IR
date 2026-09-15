import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const LOCALES = ["fa", "en"];
const DEFAULT_LOCALE = "fa";

/**
 * Locale routing without an i18n dependency: `/` negotiates from Accept-Language, everything else
 * must already carry a locale segment. Static assets and the proxied API are left alone.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasLocale = LOCALES.some((locale) => pathname === `/${locale}` || pathname.startsWith(`/${locale}/`));
  if (hasLocale) return NextResponse.next();

  if (pathname.startsWith("/api") || pathname.startsWith("/_next") || pathname.includes(".")) {
    return NextResponse.next();
  }

  const header = request.headers.get("accept-language") ?? "";
  const preferred = LOCALES.find((locale) => header.toLowerCase().includes(locale));
  // Persian is the primary language of the product; English is opt-in, never a silent default
  // for a browser that merely happens to speak it.
  const target = preferred && header.toLowerCase().startsWith("en") ? "en" : DEFAULT_LOCALE;
  const url = request.nextUrl.clone();
  url.pathname = `/${target}${pathname === "/" ? "" : pathname}`;
  url.search = request.nextUrl.search;
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
