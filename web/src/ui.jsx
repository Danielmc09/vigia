// Shared pieces of both views: strings from the shell, verdict tags, the table that turns
// into stacked cards on a phone, and the stylesheet (injected; the CSP allows inline style).
import React from "react";

const STRINGS = JSON.parse(document.getElementById("strings")?.textContent || "{}");

/** A string from the shell's catalog, with {placeholders} filled; the key if missing. */
export function t(key, values = {}) {
  const text = STRINGS[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
}

export function has(key) {
  return key in STRINGS;
}

const BOGOTA = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Bogota",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "MM-DD HH:MM" in Colombia time, like the server-rendered panel. */
export function when(iso) {
  const parts = Object.fromEntries(BOGOTA.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

/** A verdict told apart by glyph and border as well as color (color-blind readers). */
export function Tag({ label }) {
  return <span className={`tag l-${label}`}>{t(`verdict.label.${label}`)}</span>;
}

/**
 * A table on a wide screen, a stack of cards below 1100 px: every cell carries its column
 * name, and the cells marked `lead` go first as the card's heading.
 */
export function Rows({ columns, rows, empty }) {
  return (
    <table className="rows">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} className={`c-${c.key}`}>{c.title}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr className="empty">
            <td colSpan={columns.length}>{empty}</td>
          </tr>
        )}
        {rows.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => {
              const value = c.cell(row);
              const blank = value === "" || value == null || (Array.isArray(value) && !value.length);
              return (
                <td
                  key={c.key}
                  className={`c-${c.key}${c.lead ? " lead" : ""}${blank ? " blank" : ""}`}
                  data-label={c.title}
                >
                  {value}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Chips({ names, kind }) {
  return names.map((name) => (
    <span key={name} className={`chip ${kind}`}>{name}</span>
  ));
}

export const STYLE = `
:root{color-scheme:light dark;--bg:#f5f6f8;--surface:#fff;--fg:#1b1f24;--muted:#5b6470;
--line:#e2e5e9;--accent:#1a56db;--on-accent:#fff;--zebra:#fafbfc;
--bad:#a50e0e;--bad-bg:#fde8e7;--ok:#0d652d;--ok-bg:#e6f4ea;
--maybe:#8a4b00;--maybe-bg:#fef3e0;--sig:#7a4a00;--sig-bg:#fff6e5;--sig-edge:#e3a008}
@media (prefers-color-scheme: dark){:root{--bg:#0f1115;--surface:#171a20;--fg:#e6e8eb;
--muted:#9aa3ad;--line:#2a2f37;--accent:#7aa2ff;--on-accent:#0b1020;--zebra:#1b1f26;
--bad:#ffb4ab;--bad-bg:#3b1715;--ok:#8fd6a3;--ok-bg:#12321d;
--maybe:#ffc77a;--maybe-bg:#3a2a0f;--sig:#ffd28a;--sig-bg:#2e2412;--sig-edge:#b7791f}}
*{box-sizing:border-box}
html,body{overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px;display:grid;
grid-template-columns:minmax(0,1fr);gap:24px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 12px}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}
.today{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px;
min-width:0}
.try{border:2px solid var(--accent)}
.try h2{font-size:20px}
.note{color:var(--muted);font-size:13px;margin:8px 0 0;overflow-wrap:anywhere}
.error{color:var(--bad);background:var(--bad-bg);border:1px dashed var(--bad);border-radius:8px;
padding:8px 12px;font-size:14px;margin:12px 0 0}
.error::before{content:"! ";font-weight:700}
textarea{width:100%;min-height:96px;padding:12px;font:inherit;color:inherit;
background:var(--bg);border:1px solid var(--line);border-radius:8px;resize:vertical}
textarea:focus,button:focus-visible,input:focus-visible{outline:2px solid var(--accent);
outline-offset:2px}
.actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
gap:12px;margin-top:12px}
label{color:var(--muted);font-size:13px;min-width:0;max-width:100%}
input[type=file]{display:block;margin-top:4px;font:inherit;font-size:13px;color:var(--muted);
max-width:100%}
button{font:inherit;cursor:pointer;min-height:40px;border-radius:8px}
button:disabled{cursor:progress;opacity:.75}
.primary{background:var(--accent);color:var(--on-accent);border:0;padding:8px 28px;
font-weight:600;position:relative}
.primary.busy{padding-right:48px}
.primary.busy::after{content:"";position:absolute;right:14px;top:50%;width:16px;height:16px;
margin-top:-8px;border:2px solid var(--on-accent);border-right-color:transparent;
border-radius:50%;animation:vigia-spin .6s linear infinite}
@keyframes vigia-spin{to{transform:rotate(360deg)}}
.examples{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:16px;
padding-top:16px;border-top:1px solid var(--line)}
.examples span{color:var(--muted);font-size:13px}
.quick{background:transparent;color:var(--accent);border:1px solid var(--accent);
padding:4px 14px;border-radius:999px;font-size:13px}
.result-head{font-size:13px;color:var(--muted);margin:16px 0 4px;text-transform:uppercase;
letter-spacing:.04em}
.result{animation:vigia-result-in .4s ease-out}
@keyframes vigia-result-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){.primary.busy::after,.result{animation:none}}
.frame{border:1px solid var(--line);border-radius:12px;background:var(--surface);
max-height:70vh;overflow-y:auto}
.try .frame{max-height:none}
.rows{width:100%;border-collapse:collapse}
.rows th,.rows td{text-align:left;padding:10px 12px;vertical-align:top;
border-bottom:1px solid var(--line);overflow-wrap:break-word}
.rows th,.c-label,.c-brand,.c-kind{white-space:nowrap}
.rows thead th{position:sticky;top:0;z-index:1;background:var(--surface);color:var(--muted);
font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.rows tbody tr:nth-child(even){background:var(--zebra)}
.rows tbody tr:last-child td{border-bottom:0}
.c-time,.c-when,.c-confidence,.c-usd,.c-ms{white-space:nowrap;font-variant-numeric:tabular-nums}
.c-confidence,.c-usd,.c-ms{text-align:right}
.tag{display:inline-block;white-space:nowrap;border:1px solid;border-radius:6px;
padding:1px 8px;font-size:13px;font-weight:600}
.l-scam{color:var(--bad);background:var(--bad-bg);border-style:solid}
.l-scam::before{content:"\\2715";margin-right:.35em}
.l-legit{color:var(--ok);background:var(--ok-bg);border-style:solid}
.l-legit::before{content:"\\2713";margin-right:.35em}
.l-doubtful{color:var(--maybe);background:var(--maybe-bg);border-style:dashed}
.l-doubtful::before{content:"?";margin-right:.35em}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{border-radius:4px;padding:1px 6px;font-size:12px;overflow-wrap:break-word;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.sig{color:var(--sig);background:var(--sig-bg);border-left:3px solid var(--sig-edge)}
.tool{color:var(--muted);border:1px solid var(--line)}
.warn{color:var(--bad);background:var(--bad-bg);border:1px dashed var(--bad)}
.warn::before{content:"!";margin-right:.35em}
.metrics h2{font-size:14px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:12px;
color:var(--muted);overflow-wrap:anywhere}
.card b{display:block;font-size:16px;color:var(--fg);font-variant-numeric:tabular-nums}
.today .card{display:flex;gap:6px;align-items:baseline;border-radius:999px;padding:2px 12px;
background:var(--surface)}.today .card b{font-size:14px}
.split{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:24px}
@media (max-width:1100px){
main{padding:16px;gap:16px}.panel{padding:16px}
.primary{width:100%}.actions{align-items:stretch}.actions label{width:100%}
.frame{max-height:none;border:0;background:transparent}
.rows thead{display:none}
.rows,.rows tbody{display:block}
.rows tbody tr{display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:12px;padding:12px;
background:var(--surface);border:1px solid var(--line);border-radius:12px}
.rows tbody tr:nth-child(even){background:var(--surface)}
.rows td{display:grid;grid-template-columns:7.5em minmax(0,1fr);gap:8px;width:100%;
padding:2px 0;border:0;text-align:left}
.rows td::before{content:attr(data-label);color:var(--muted);font-size:12px;
text-transform:uppercase;letter-spacing:.04em}
.rows td{white-space:normal}
.rows td.lead{order:-1;display:block;width:auto;font-weight:600;font-size:16px;padding-bottom:6px}
.rows td.lead::before{content:none}
.rows td.blank{display:none}
.rows tr.empty td{display:block}.rows tr.empty td::before{content:none}
}
`;
