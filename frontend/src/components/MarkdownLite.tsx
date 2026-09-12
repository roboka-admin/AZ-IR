"use client";

/**
 * Minimal Markdown renderer for editorial prose.
 *
 * A dependency-free subset (## / ### headings, paragraphs, `-` lists, `>` quotes, `**bold**`)
 * is enough for articles and keeps the bundle small (AGENTS.md rule 13). It also does one
 * atlas-specific job: the first mention of every linked entity becomes a link, which is what
 * makes Article -> Entity -> Map bidirectional in practice (handoff §12).
 */

import { Fragment, type ReactNode } from "react";
import Link from "next/link";

export interface BodyLink {
  label: string;
  href: string;
}

export interface MarkdownLiteProps {
  body: string | null;
  links?: BodyLink[];
  locale: "fa" | "en";
}

function inline(text: string, keyBase: string, links: BodyLink[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Split on **bold** first; entity linking is applied to the plain segments.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  parts.forEach((part, partIndex) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**")) {
      nodes.push(<strong key={`${keyBase}-b${partIndex}`}>{part.slice(2, -2)}</strong>);
      return;
    }
    nodes.push(...linkify(part, `${keyBase}-t${partIndex}`, links));
  });
  return nodes;
}

function linkify(text: string, key: string, links: BodyLink[]): ReactNode[] {
  if (links.length === 0) return [<Fragment key={key}>{text}</Fragment>];
  const pattern = links
    .map((link) => link.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .filter(Boolean)
    .join("|");
  if (!pattern) return [<Fragment key={key}>{text}</Fragment>];
  const regex = new RegExp(`(${pattern})`, "g");
  return text.split(regex).map((chunk, index) => {
    const match = links.find((link) => link.label === chunk);
    if (match) {
      return (
        <Link key={`${key}-${index}`} href={match.href} className="rel-object">
          {chunk}
        </Link>
      );
    }
    return <Fragment key={`${key}-${index}`}>{chunk}</Fragment>;
  });
}

export default function MarkdownLite({ body, links = [], locale }: MarkdownLiteProps) {
  if (!body || !body.trim()) {
    return (
      <p className="prose" style={{ color: "var(--text-faint)" }}>
        {locale === "fa" ? "متنی برای این زبان ثبت نشده است." : "No text recorded for this language yet."}
      </p>
    );
  }

  const blocks = body.split(/\n{2,}/);
  const used = new Set<string>();

  // Only the first mention of an entity is linked, so long prose stays readable.
  const activeLinks = links.filter((link) => {
    if (used.has(link.label)) return false;
    if (!body.includes(link.label)) return false;
    used.add(link.label);
    return true;
  });

  return (
    <div className="prose" dir={locale === "fa" ? "rtl" : "ltr"}>
      {blocks.map((block, index) => {
        const text = block.trim();
        if (!text) return null;
        const key = `b${index}`;
        if (text.startsWith("### ")) {
          return <h3 key={key}>{inline(text.slice(4), key, activeLinks)}</h3>;
        }
        if (text.startsWith("## ")) {
          return <h2 key={key}>{inline(text.slice(3), key, activeLinks)}</h2>;
        }
        if (text.startsWith("> ")) {
          return <blockquote key={key}>{inline(text.slice(2), key, activeLinks)}</blockquote>;
        }
        if (/^[-*] /.test(text)) {
          return (
            <ul key={key}>
              {text.split("\n").map((line, lineIndex) => (
                <li key={`${key}-li${lineIndex}`}>{inline(line.replace(/^[-*] /, ""), `${key}-${lineIndex}`, activeLinks)}</li>
              ))}
            </ul>
          );
        }
        return <p key={key}>{inline(text.replace(/\n/g, " "), key, activeLinks)}</p>;
      })}
    </div>
  );
}
