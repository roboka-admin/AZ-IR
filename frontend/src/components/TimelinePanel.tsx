"use client";

/**
 * The timeline: temporal zoom + scrubbing + playback.
 *
 * Time is a first-class axis here, not a filter widget: the visible window can be zoomed
 * (centuries -> decades -> single years), the histogram shows how much is known per bucket, and
 * period bands come from the API's region-aware periodization (ADR-0012) rather than from a
 * hard-coded list in the component (AGENTS.md rule 19).
 */

import { calendarLabel, formatNumber, formatYear, localizeDigits, modeLabel, t } from "@/lib/i18n";
import { uniqueById } from "@/lib/timeline";
import type { CalendarCode, MetaResponse, TemporalMode, TimelineBucket } from "@/lib/types";

export interface TimelinePanelProps {
  locale: "fa" | "en";
  meta: MetaResponse;
  year: number;
  calendar: CalendarCode;
  mode: TemporalMode;
  span: [number, number] | null;
  window: [number, number];
  buckets: TimelineBucket[];
  playing: boolean;
  calendarDisplay: { from: number; to: number; calendar: CalendarCode } | null;
  onYearChange: (year: number) => void;
  onWindowChange: (window: [number, number]) => void;
  onCalendarChange: (calendar: CalendarCode) => void;
  onModeChange: (mode: TemporalMode) => void;
  onSpanChange: (span: [number, number] | null) => void;
  onTogglePlay: () => void;
  onOpenEntity: (entityType: string, id: string) => void;
}

const PERIOD_COLORS = [
  "#6d4fa3", "#3f6ea5", "#1f6f78", "#b0562c", "#a1762a", "#7c6a4b", "#8e1b1b", "#2f7d32", "#5c6672",
];

function ratio(year: number, from: number, to: number): number {
  if (to <= from) return 0;
  return Math.min(1, Math.max(0, (year - from) / (to - from)));
}

export default function TimelinePanel({
  locale,
  meta,
  year,
  calendar,
  mode,
  span,
  window: timeWindow,
  buckets,
  playing,
  calendarDisplay,
  onYearChange,
  onWindowChange,
  onCalendarChange,
  onModeChange,
  onSpanChange,
  onTogglePlay,
  onOpenEntity,
}: TimelinePanelProps) {
  const [from, to] = timeWindow;
  const floor = meta.timeline.floor;
  const ceil = meta.timeline.ceil;
  const spanYears = Math.max(1, to - from);
  const maxTotal = buckets.reduce((max, bucket) => Math.max(max, bucket.total), 1);
  const position = ratio(year, from, to);
  const currentPeriod = meta.periods.find(
    (period) => period.year_from !== null && period.year_to !== null && year >= period.year_from && year <= period.year_to,
  );
  // Long-running events can overlap multiple histogram buckets. The API correctly includes them in
  // each bucket's count, but the entity strip must show one button per event (and one React key).
  const nearbyNotable = uniqueById(
    buckets
      .filter((bucket) => bucket.notable.length > 0)
      .flatMap((bucket) => bucket.notable)
      .filter((event) => Math.abs(event.year - year) <= Math.max(6, spanYears / 6)),
  ).slice(0, 8);

  const zoomTime = (factor: number) => {
    const next = Math.min(ceil - floor, Math.max(4, Math.round(spanYears * factor)));
    const center = year;
    const nextFrom = Math.max(floor, Math.min(center - next / 2, ceil - next));
    onWindowChange([Math.round(nextFrom), Math.round(nextFrom + next)]);
  };

  const calendars = meta.timeline.calendars.filter((code) => code !== "unknown");

  return (
    <section className="panel timeline" aria-label={t(locale, "ui.time.title")}>
      <div className="timeline-head">
        <button type="button" className="btn btn-icon" onClick={onTogglePlay} aria-pressed={playing}
          title={playing ? t(locale, "ui.time.pause") : t(locale, "ui.time.play")}>
          {playing ? "❚❚" : "▶"}
        </button>

        <div>
          <div className="year-big">
            {calendarDisplay && calendarDisplay.calendar !== "gregorian_proleptic"
              ? localizeDigits(calendarDisplay.from, locale)
              : formatYear(year, locale)}
          </div>
          <div className="year-sub">
            {calendarDisplay && calendarDisplay.calendar !== "gregorian_proleptic"
              ? `${calendarLabel(locale, calendarDisplay.calendar)}: ${formatYear(calendarDisplay.from, locale)}${
                  calendarDisplay.to !== calendarDisplay.from ? `–${localizeDigits(calendarDisplay.to, locale)}` : ""
                }`
              : currentPeriod
                ? currentPeriod.label
                : t(locale, "ui.time.year")}
          </div>
        </div>

        <div className="row" style={{ gap: 4 }}>
          <button
            type="button"
            className="btn btn-icon"
            onClick={() => zoomTime(0.5)}
            title={t(locale, "ui.time.zoomIn")}
            aria-label={t(locale, "ui.time.zoomIn")}
          >＋</button>
          <button
            type="button"
            className="btn btn-icon"
            onClick={() => zoomTime(2)}
            title={t(locale, "ui.time.zoomOut")}
            aria-label={t(locale, "ui.time.zoomOut")}
          >－</button>
          <button
            type="button"
            className="btn"
            onClick={() => onWindowChange([floor, ceil])}
            title={t(locale, "ui.time.reset")}
          >
            {formatNumber(floor, locale)}…{formatNumber(ceil, locale)}
          </button>
        </div>

        <div className="grow" />

        <label className="row" style={{ gap: 6, fontSize: 11.5, color: "var(--text-dim)" }}>
          {t(locale, "ui.time.calendar")}
          <select
            className="field"
            style={{ width: "auto", padding: "4px 8px" }}
            value={calendar}
            onChange={(event) => onCalendarChange(event.target.value as CalendarCode)}
          >
            {calendars.map((code) => (
              <option key={code} value={code}>
                {calendarLabel(locale, code)}
              </option>
            ))}
          </select>
        </label>

        <label className="row" style={{ gap: 6, fontSize: 11.5, color: "var(--text-dim)" }}>
          {t(locale, "ui.time.mode")}
          <select
            className="field"
            style={{ width: "auto", padding: "4px 8px" }}
            value={mode}
            disabled={!span}
            title={!span ? t(locale, "ui.time.modeNeedsSpan") : undefined}
            onChange={(event) => onModeChange(event.target.value as TemporalMode)}
          >
            {(["at", "overlaps", "during"] as TemporalMode[]).map((code) => (
              <option key={code} value={code}>
                {modeLabel(locale, code)}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className="btn"
          aria-pressed={Boolean(span)}
          onClick={() => onSpanChange(span ? null : [year - 25, year + 25])}
          title={t(locale, "ui.time.span")}
        >
          {t(locale, "ui.time.span")}
        </button>
      </div>

      {span ? (
        <div className="timeline-range-fields">
          <label>
            <span>{t(locale, "ui.time.from")}</span>
            <input
              key={`from-${span[0]}`}
              className="field"
              type="number"
              min={floor}
              max={ceil}
              defaultValue={span[0]}
              onBlur={(event) => onSpanChange([
                Number.isFinite(event.currentTarget.valueAsNumber) ? event.currentTarget.valueAsNumber : span[0],
                span[1],
              ])}
            />
          </label>
          <span aria-hidden>—</span>
          <label>
            <span>{t(locale, "ui.time.to")}</span>
            <input
              key={`to-${span[1]}`}
              className="field"
              type="number"
              min={floor}
              max={ceil}
              defaultValue={span[1]}
              onBlur={(event) => onSpanChange([
                span[0],
                Number.isFinite(event.currentTarget.valueAsNumber) ? event.currentTarget.valueAsNumber : span[1],
              ])}
            />
          </label>
          <span className="year-sub">{modeLabel(locale, mode)}</span>
        </div>
      ) : null}

      <div className="periods" role="group" aria-label={t(locale, "ui.time.periods")}>
        {meta.periods.map((period, index) => {
          const yearFrom = period.year_from;
          const yearTo = period.year_to;
          if (yearFrom === null || yearTo === null) return null;
          const left = ratio(yearFrom, from, to);
          const right = ratio(yearTo, from, to);
          const width = right - left;
          if (width <= 0) return null;
          return (
            <button
              key={period.id}
              type="button"
              className="period-band"
              style={{
                left: `${left * 100}%`,
                width: `${width * 100}%`,
                background: PERIOD_COLORS[index % PERIOD_COLORS.length],
                opacity: year >= yearFrom && year <= yearTo ? 0.95 : 0.5,
              }}
              title={`${period.label} (${formatYear(yearFrom, locale)}–${formatYear(yearTo, locale)})`}
              aria-label={`${period.label}: ${formatYear(yearFrom, locale)}–${formatYear(yearTo, locale)}`}
              onClick={() => onYearChange(Math.round((yearFrom + yearTo) / 2))}
            >
              {width > 0.06 ? period.label : ""}
            </button>
          );
        })}
      </div>

      <div className="histogram" role="group" aria-label={t(locale, "ui.time.distribution")}>
        {buckets.length === 0 ? (
          <div className="year-sub">{t(locale, "ui.loading")}</div>
        ) : (
          buckets.map((bucket) => (
            <button
              key={bucket.from}
              type="button"
              className={`histogram-bar${bucket.notable.length > 0 ? " notable" : ""}`}
              style={{ height: `${Math.max(4, (bucket.total / maxTotal) * 100)}%` }}
              title={`${localizeDigits(bucket.display_from ?? bucket.from, locale)}–${localizeDigits(bucket.display_to ?? bucket.to, locale)}: ${formatNumber(bucket.total, locale)}`}
              aria-label={t(locale, "ui.time.bucketLabel", {
                from: localizeDigits(bucket.display_from ?? bucket.from, locale),
                to: localizeDigits(bucket.display_to ?? bucket.to, locale),
                count: formatNumber(bucket.total, locale),
              })}
              onClick={() => onYearChange(Math.round((bucket.from + bucket.to) / 2))}
            />
          ))
        )}
      </div>

      <div className="scrubber">
        <input
          type="range"
          min={from}
          max={to}
          step={spanYears > 400 ? 5 : 1}
          value={Math.min(to, Math.max(from, year))}
          onChange={(event) => onYearChange(Number(event.target.value))}
          aria-label={t(locale, "ui.time.year")}
          aria-valuetext={formatYear(year, locale)}
        />
        <span
          style={{
            position: "absolute",
            insetInlineStart: `calc(${position * 100}% )`,
            top: -2,
            width: 2,
            height: 30,
            background: "var(--accent)",
            opacity: 0.35,
            pointerEvents: "none",
          }}
        />
      </div>

      <div className="row" style={{ gap: 8 }}>
        <span className="chip">
          {t(locale, "ui.time.bucket", { bucket: buckets[0] ? buckets[0].to - buckets[0].from + 1 : 0 })}
        </span>
        <div className="notable-strip grow">
          {nearbyNotable.map((event) => (
            <button
              key={event.id}
              type="button"
              className="notable-pill"
              onClick={() => onOpenEntity("event", event.id)}
              title={`${event.label} (${localizeDigits(event.display_year ?? event.year, locale)})`}
            >
              {event.label} · {localizeDigits(event.display_year ?? event.year, locale)}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
