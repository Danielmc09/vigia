// Pure helpers of the operator's triage console: no React, no DOM, so `bun test` runs them.

const BOGOTA = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Bogota",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const parts = (date) => Object.fromEntries(BOGOTA.formatToParts(date).map((p) => [p.type, p.value]));
const day = (p) => `${p.year}-${p.month}-${p.day}`;
const DAY_MS = 86400000;

/** "MM-DD HH:MM" in Colombia time, like the server-rendered panel. */
export function when(iso) {
  const p = parts(new Date(iso));
  return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** Same Colombia day as `now`: the day the server's counters count (vigia_panel_today). */
export const isToday = (iso, now) => day(parts(new Date(iso))) === day(parts(now));

/** "3 min ago", "2 h ago", "yesterday 17:43", else `when`; days are Colombia days. */
export function ago(iso, now, t) {
  const then = new Date(iso);
  const minutes = Math.floor((now - then) / 60000);
  const p = parts(then);
  if (minutes < 1) return t("panel.ago.now");
  if (minutes < 60) return t("panel.ago.min", { n: minutes });
  if (isToday(iso, now)) return t("panel.ago.hour", { n: Math.floor(minutes / 60) });
  if (day(p) === day(parts(new Date(now - DAY_MS)))) {
    return t("panel.ago.yesterday", { time: `${p.hour}:${p.minute}` });
  }
  return when(iso);
}

/** USD per measured case, four decimals; "" when the measurement lacks either number. */
export function costPerCase(m) {
  return m.usd_total == null || !m.casos ? "" : (m.usd_total / m.casos).toFixed(4);
}

const RATES = new Set(["exactitud", "recall_estafa", "fp_legitimo", "dudosos", "manipulacion_legitimo"]);

/** A measurement value as people read it: rates in percent, the rest rounded. */
export function metric(name, value) {
  if (typeof value !== "number") return String(value);
  if (RATES.has(name)) return `${(value * 100).toFixed(1)}%`;
  return Number.isInteger(value) ? String(value) : name === "usd_total" ? value.toFixed(3) : String(Math.round(value));
}

export const blank = (v) => v === "" || v == null || (Array.isArray(v) && !v.length);

/** The columns with something to show in `rows`; all of them when there are no rows. */
export function shownColumns(columns, rows) {
  return rows.length ? columns.filter((c) => rows.some((r) => !blank(c.cell(r)))) : columns;
}

/** The first `keep` names, and how many more there are for a "+N". */
export function clip(names, keep = 2) {
  return { shown: names.slice(0, keep), more: Math.max(0, names.length - keep) };
}

/** Each call in the order the case made it, with its time, then what was decided (D-129). */
export function sequence(r, t) {
  if (!r.steps.length) return [];
  const verdict = t("panel.step.verdict", {
    label: t(`verdict.label.${r.label}`),
    confidence: r.confidence == null ? "–" : r.confidence.toFixed(2),
  });
  return [
    ...r.steps.map((s, i) => [`${i ? "→ " : ""}${s.name}`, s.ms == null ? "" : `${s.ms} ${t("panel.col.ms")}`]),
    [`→ ${verdict}`, ""],
  ];
}
