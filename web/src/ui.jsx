// Shared pieces of both views: strings from the shell, verdict tags, the table that turns
// into stacked cards on a phone, and the stylesheet (injected; the CSP allows inline style).
import React, { Fragment, useState } from "react";
import { blank, shownColumns, when } from "./triage.js";

export { when };

const STRINGS = JSON.parse(document.getElementById("strings")?.textContent || "{}");

/** A string from the shell's catalog, with {placeholders} filled; the key if missing. */
export function t(key, values = {}) {
  const text = STRINGS[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
}

export function has(key) {
  return key in STRINGS;
}

/** A verdict told apart by glyph and border as well as color (color-blind readers). */
export function Tag({ label }) {
  return <span className={`tag l-${label}`}>{t(`verdict.label.${label}`)}</span>;
}

/**
 * A table on a wide screen, a stack of cards below 1100 px: every cell carries its column
 * name, and the cells marked `lead` go first as the card's heading. A column with nothing
 * to show in `rows` is not drawn. With `detail(row)` -> [{key, title, value}], each row gets
 * a disclosure button (keyboard and touch, not hover) that opens the non-blank entries below.
 */
export function Rows({ columns, rows, empty, detail }) {
  const shown = shownColumns(columns, rows);
  const [open, setOpen] = useState(null);
  const toggle = (row) => setOpen((o) => (o === row ? null : row));
  return (
    <table className="rows">
      <thead>
        <tr>
          {shown.map((c) => (
            <th key={c.key} className={`c-${c.key}`}>{c.title}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr className="empty">
            <td colSpan={shown.length}>{empty}</td>
          </tr>
        )}
        {rows.map((row, i) => (
          <Fragment key={i}>
            <Row columns={shown} row={row} openable={!!detail} open={open === row} toggle={() => toggle(row)} />
            {detail && open === row && <Detail entries={detail(row)} span={shown.length} />}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

/** One row; when `openable`, its first cell carries the disclosure button. */
function Row({ columns, row, openable, open, toggle }) {
  return (
    <tr className={openable ? "openable" : undefined} onClick={openable ? toggle : undefined}>
      {columns.map((c, j) => {
        const value = c.cell(row);
        return (
          <td
            key={c.key}
            className={`c-${c.key}${c.lead ? " lead" : ""}${blank(value) ? " blank" : ""}`}
            data-label={c.title}
          >
            {openable && j === 0 && (
              <button
                type="button"
                className="more"
                aria-expanded={open}
                aria-label={t(open ? "panel.detail.close" : "panel.detail")}
                onClick={(e) => {
                  e.stopPropagation();
                  toggle();
                }}
              />
            )}
            {value}
          </td>
        );
      })}
    </tr>
  );
}

/** The opened row's extra entries, blank ones dropped. */
function Detail({ entries, span }) {
  return (
    <tr className="detail">
      <td colSpan={span}>
        <dl>
          {entries
            .filter((d) => !blank(d.value))
            .map((d) => (
              <Fragment key={d.key}>
                <dt>{d.title}</dt>
                <dd className={`d-${d.key}`}>{d.value}</dd>
              </Fragment>
            ))}
        </dl>
      </td>
    </tr>
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
.note{color:var(--muted);font-size:13px;margin:8px 0 0;overflow-wrap:anywhere}
.error{color:var(--bad);background:var(--bad-bg);border:1px dashed var(--bad);border-radius:8px;
padding:8px 12px;font-size:14px;margin:12px 0 0}
.error::before{content:"! ";font-weight:700}
textarea{flex:1 1 20em;min-width:0;min-height:40px;max-height:30vh;padding:8px 12px;
font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;
resize:none;field-sizing:content}
textarea:focus,button:focus-visible,.file:focus-within{outline:2px solid var(--accent);
outline-offset:2px}
.ask{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px}
.file{position:relative;display:inline-flex;align-items:center;min-height:40px;max-width:14em;
padding:0 12px;border:1px solid var(--line);border-radius:8px;color:var(--muted);font-size:13px;
cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.file input{position:absolute;inset:0;opacity:0;width:100%;cursor:pointer}
button{font:inherit;cursor:pointer;min-height:40px;border-radius:8px}
button:disabled{cursor:progress;opacity:.75}
.primary{background:var(--accent);color:var(--on-accent);border:0;padding:8px 28px;
font-weight:600;position:relative}
.primary.busy{padding-right:48px}
.primary.busy::after{content:"";position:absolute;right:14px;top:50%;width:16px;height:16px;
margin-top:-8px;border:2px solid var(--on-accent);border-right-color:transparent;
border-radius:50%;animation:vigia-spin .6s linear infinite}
@keyframes vigia-spin{to{transform:rotate(360deg)}}
.examples{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
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
.rows th,.rows td{text-align:left;padding:6px 10px;vertical-align:middle;font-size:14px;
border-bottom:1px solid var(--line);overflow-wrap:break-word}
.rows th,.c-label,.c-brand,.c-kind,.c-time{white-space:nowrap}
.c-label{width:1%}
.c-signals{width:100%}
.rows tr.openable{cursor:pointer}
.more{min-height:0;width:24px;height:24px;margin-right:6px;padding:0;vertical-align:middle;
background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:6px}
.more::before{content:"\\25B8";display:inline-block;font-size:16px;line-height:1}
.more[aria-expanded=true]::before{transform:rotate(90deg)}
.rows tr.detail td{background:var(--bg);padding:8px 10px 12px 44px}
.rows tr.detail dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 16px;
margin:0;font-size:13px}
.rows tr.detail dt{color:var(--muted)}.rows tr.detail dd{margin:0;min-width:0}
.d-confidence,.d-usd,.d-ms{font-variant-numeric:tabular-nums}
.c-signals .chips{flex-wrap:nowrap;overflow:hidden}
.c-signals .chip{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:16em}
.rows thead th{position:sticky;top:0;z-index:1;background:var(--surface);color:var(--muted);
font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.rows tbody tr:nth-child(even){background:var(--zebra)}
.rows tbody tr:last-child td{border-bottom:0}
.c-time,.c-when{font-variant-numeric:tabular-nums}
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
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.card{border:1px solid var(--line);border-radius:8px;padding:12px 16px;font-size:13px;
color:var(--muted);background:var(--surface);overflow-wrap:anywhere}
.card b{display:block;font-size:24px;color:var(--fg);font-variant-numeric:tabular-nums}
.today button{display:inline-flex;gap:6px;align-items:center;min-height:32px;padding:2px 12px;
border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--muted);
font-size:13px}
.today button b{font-size:14px;color:var(--fg);font-variant-numeric:tabular-nums}
.today button[aria-pressed=true]{background:var(--fg);border-color:var(--fg);color:var(--bg)}
.today button[aria-pressed=true] b{color:var(--bg)}
.today .clear{color:var(--accent);border-color:var(--accent)}
.owner{max-width:880px}
.reports{display:grid;gap:12px}
.report{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:12px;padding:12px 16px}
.r-scam{border-left-color:var(--bad)}.r-doubtful{border-left-color:var(--maybe)}
.report-head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px}
.report-head .note{margin:0}
.report p{margin:8px 0 0;overflow-wrap:anywhere}
.todo{font-weight:600}.r-legit .todo{font-weight:400;color:var(--muted)}
.split{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:24px}
@media (max-width:1100px){
main{padding:16px;gap:16px}.panel{padding:16px}
.ask textarea{flex-basis:100%}.primary{flex:1}.file{max-width:none}
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
.rows tr.empty td,.rows tr.detail td{display:block}
.rows tr.empty td::before,.rows tr.detail td::before{content:none}
.rows tbody tr.detail{margin-top:-12px;border-top:0;border-radius:0 0 12px 12px}
.rows tr.detail td{padding:8px 0 0}
.c-signals .chips{flex-wrap:wrap}
.rows td.c-time,.rows td.c-kind{display:inline;width:auto;padding:0;font-size:12px;
color:var(--muted)}
.rows td.c-time::before{content:none}
.rows td.c-kind::before{content:"\\00b7\\0020"}
.examples{flex-wrap:nowrap;overflow-x:auto;padding-bottom:2px}
.examples span,.quick{flex:0 0 auto}
.metrics .cards{grid-template-columns:1fr}
}
`;
