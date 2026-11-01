// The two React views of the panel (D-121): the operator's, behind the operator token, and
// the owner's, behind the session cookie from /entrar. The server picks one in #root.
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ago, clip, costPerCase, isToday, metric } from "./triage.js";
import { Chips, Rows, STYLE, Tag, has, t, when } from "./ui.jsx";

const EXAMPLES = ["bank", "courier", "legit"];
const TIMEOUT_MS = 30000;

async function call(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    signal: AbortSignal.timeout(TIMEOUT_MS),
    ...options,
  });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

/** Fetch once on mount; `reload` fetches again (after a try). */
function useApi(url) {
  const [state, setState] = useState({ data: null, failed: false });
  const reload = () =>
    call(url).then(
      (data) => setState({ data, failed: false }),
      () => setState((s) => ({ ...s, failed: true })),
    );
  useEffect(() => {
    reload();
  }, []);
  return [state, reload];
}

const fixed = (digits) => (n) => (n == null ? "" : Number(n).toFixed(digits));
// Rules-only verdicts cost nothing and wait for no analyst: 0 there means "not measured".
const spent = (digits) => (n) => (n ? fixed(digits)(n) : "");

const chips = (names, kind) => (names.length ? <div className="chips"><Chips names={names} kind={kind} /></div> : "");

function Signals({ names }) {
  const { shown, more } = clip(names);
  return (
    <div className="chips" title={names.join(", ")}>
      <Chips names={shown} kind="sig" />
      {more > 0 && <span className="chip tool">+{more}</span>}
    </div>
  );
}

const VERDICT_COLUMNS = [
  { key: "label", title: t("panel.col.label"), lead: true, cell: (r) => <Tag label={r.label} /> },
  {
    key: "time",
    title: t("panel.col.time"),
    cell: (r) => <time dateTime={r.created_at} title={when(r.created_at)}>{ago(r.created_at, new Date(), t)}</time>,
  },
  { key: "kind", title: t("panel.col.kind"), cell: (r) => r.kind || "" },
  { key: "brand", title: t("panel.col.brand"), lead: true, cell: (r) => r.brand || "" },
  {
    key: "signals",
    title: t("panel.col.signals"),
    cell: (r) => (r.signal_types.length ? <Signals names={r.signal_types} /> : ""),
  },
];

/** What a row keeps out of the table: blank entries are dropped, so rules mode shows less. */
const verdictDetail = (r) => [
  { key: "confidence", title: t("panel.col.confidence"), value: fixed(2)(r.confidence) },
  { key: "signals", title: t("panel.col.signals"), value: chips(r.signal_types, "sig") },
  {
    key: "tools",
    title: t("panel.col.tools"),
    value:
      r.tools.length || r.alert_rejections.length ? (
        <div className="chips">
          <Chips names={r.tools} kind="tool" />
          <Chips names={r.alert_rejections} kind="warn" />
        </div>
      ) : "",
  },
  { key: "usd", title: t("panel.col.usd"), value: spent(4)(r.usd) },
  { key: "ms", title: t("panel.col.ms"), value: spent(0)(r.latency_ms) },
];

const LABELS = ["scam", "legit", "doubtful"];

/** Today's counts, each one a filter over the table; the pressed one is the active filter. */
function Today({ today, filter, setFilter }) {
  return (
    <div className="today">
      {t("panel.today")}
      {LABELS.map((label) => (
        <button
          key={label}
          type="button"
          aria-pressed={filter === label}
          onClick={() => setFilter(filter === label ? null : label)}
        >
          {t(`verdict.label.${label}`)}
          <b>{today[label] || 0}</b>
        </button>
      ))}
      {filter && (
        <button type="button" className="clear" onClick={() => setFilter(null)}>
          {t("panel.filter.clear")} ✕
        </button>
      )}
    </div>
  );
}

function Try({ rulesOnly, onTried }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [tried, setTried] = useState(null);

  async function analyze(body) {
    setBusy(true);
    setFailed(false);
    try {
      const result = await call("/api/operator/try", { method: "POST", body });
      setTried(result);
      setText(result.text);
      onTried();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  function submit(event) {
    event.preventDefault();
    analyze(new FormData(event.currentTarget));
  }

  function example(name) {
    const body = new FormData();
    body.set("text", t(`panel.try.example.${name}.text`));
    analyze(body);
  }

  // Enter sends, Shift+Enter breaks the line (not while an IME is composing).
  function keyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form.requestSubmit();
    }
  }

  const notes = tried
    ? [
        tried.notice,
        tried.read && `${t("panel.try.read")}: ${tried.read}`,
        tried.links.length && `${t("panel.try.links")}: ${tried.links.join(" ")}`,
      ].filter(Boolean)
    : [];
  return (
    <section className="panel try" aria-busy={busy}>
      <h2>{t("panel.try.title")}</h2>
      <form onSubmit={submit} className="ask">
        <textarea
          name="text"
          rows={1}
          value={text}
          placeholder={t("panel.try.placeholder")}
          aria-label={t("panel.try.title")}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={keyDown}
        />
        <label className="file" title={file || t("panel.try.file.hint")}>
          {file || t("panel.try.file")}
          <input
            type="file"
            name="file"
            accept="audio/*,image/*,application/pdf"
            onChange={(e) => setFile(e.target.files[0]?.name || "")}
          />
        </label>
        <button className={`primary${busy ? " busy" : ""}`} type="submit" disabled={busy}>
          {busy ? t("panel.try.busy") : t("panel.try.button")}
        </button>
        <div className="examples">
          <span>{t("panel.try.examples")}</span>
          {EXAMPLES.map((name) => (
            <button key={name} className="quick" type="button" disabled={busy} onClick={() => example(name)}>
              {t(`panel.try.example.${name}`)}
            </button>
          ))}
        </div>
      </form>
      {busy && <p className="note">{t("panel.try.wait")}</p>}
      {failed && <p className="error" role="alert">{t("panel.try.error")}</p>}
      {(tried?.rules_only ?? rulesOnly) && <p className="note">{t("panel.try.rules_only")}</p>}
      {notes.map((note) => (
        <p key={note} className="note">{note}</p>
      ))}
      {tried?.row && (
        <div className="result" key={tried.row.created_at}>
          <h3 className="result-head">
            {t("panel.try.result")} {when(tried.row.created_at).slice(6)}
          </h3>
          <div className="frame">
            <Rows columns={VERDICT_COLUMNS} rows={[tried.row]} detail={verdictDetail} />
          </div>
        </div>
      )}
    </section>
  );
}

const HEADLINE = ["recall_estafa", "fp_legitimo"];

/** Three numbers that answer "is it working": scams caught, false alarms, cost per case. */
function Metrics({ result }) {
  if (!result) return <p className="note">{t("panel.no_metrics")}</p>;
  const m = result.metricas || {};
  const rest = Object.entries(m).filter(
    ([name, v]) => !HEADLINE.includes(name) && ["number", "string"].includes(typeof v),
  );
  const headline = [
    [t("panel.metric.caught"), metric("recall_estafa", m.recall_estafa)],
    [t("panel.metric.false_alarms"), metric("fp_legitimo", m.fp_legitimo)],
    [t("panel.metric.cost"), costPerCase(m)],
  ];
  return (
    <section className="panel metrics">
      <h2>
        {t("panel.metrics")}
        {result.fecha ? ` · ${result.fecha}` : ""}
      </h2>
      <div className="cards">
        {headline.map(([title, value]) => (
          <div key={title} className="card">
            {title}
            <b>{value}</b>
          </div>
        ))}
      </div>
      <p className="note">
        {rest
          .map(([name, v]) => `${has(`metric.${name}`) ? t(`metric.${name}`) : name} ${metric(name, v)}`)
          .join(" · ")}
      </p>
    </section>
  );
}

function Operator() {
  const [{ data, failed }, reload] = useApi("/api/operator");
  const [filter, setFilter] = useState(null);
  // A counter counts today only, so its filter shows today's rows only.
  const now = new Date();
  const rows = data ? data.rows.filter((r) => !filter || (r.label === filter && isToday(r.created_at, now))) : [];
  return (
    <main>
      <header className="top">
        <h1>{t("panel.title")}</h1>
        {data && <Today today={data.today} filter={filter} setFilter={setFilter} />}
      </header>
      <Metrics result={data?.metrics} />
      <Try rulesOnly={data?.rules_only} onTried={reload} />
      {failed && <p className="error" role="alert">{t("panel.try.error")}</p>}
      {!data && !failed && <p className="note">{t("owner.loading")}</p>}
      {data && (
        <section>
          <h2>
            {t("panel.verdicts")}
            {filter && ` · ${t("panel.filter.only", { label: t(`verdict.label.${filter}`) })}`}
          </h2>
          <div className="frame">
            <Rows columns={VERDICT_COLUMNS} rows={rows} empty={t("panel.empty")} detail={verdictDetail} />
          </div>
        </section>
      )}
    </main>
  );
}

const kind = (k) => (k && has(`owner.kind.${k}`) ? t(`owner.kind.${k}`) : k || "");

const OWNER_TABLES = {
  team: [
    { key: "name", title: t("owner.col.name"), lead: true, cell: (m) => m.name || "" },
    { key: "phone", title: t("owner.col.phone"), cell: (m) => m.phone },
  ],
  suppliers: [
    { key: "name", title: t("owner.col.name"), lead: true, cell: (s) => s.name },
    { key: "account", title: t("owner.col.account"), cell: (s) => s.account || "" },
    { key: "email", title: t("owner.col.email"), cell: (s) => s.email || "" },
  ],
};

/** One thing the team forwarded, in the words WhatsApp used: what it is and what to do. */
function Report({ report, now }) {
  return (
    <article className={`report r-${report.label}`}>
      <div className="report-head">
        <Tag label={report.label} />
        {report.brand && <b>{report.brand}</b>}
        <span className="note">
          {kind(report.kind)} ·{" "}
          <time dateTime={report.at} title={when(report.at)}>{ago(report.at, now, t)}</time>
        </span>
      </div>
      {report.explanation && <p>{report.explanation}</p>}
      {report.action && <p className="todo">{report.action}</p>}
    </article>
  );
}

function Owner() {
  const [{ data, failed }] = useApi("/api/owner");
  if (failed) return <main><p className="error" role="alert">{t("owner.error")}</p></main>;
  if (!data) return <main><p className="note">{t("owner.loading")}</p></main>;
  const now = new Date();
  const table = (name) => (
    <section>
      <h2>{t(`owner.${name}`)}</h2>
      <div className="frame">
        <Rows columns={OWNER_TABLES[name]} rows={data[name]} empty={t(`owner.empty.${name}`)} />
      </div>
    </section>
  );
  document.title = t("owner.title", { business: data.business });
  return (
    <main className="owner">
      <header className="top">
        <h1>{document.title}</h1>
      </header>
      <section>
        <h2>{t("owner.cases")}</h2>
        {data.cases.length ? (
          <div className="reports">
            {data.cases.map((c, i) => <Report key={`${c.at}-${i}`} report={c} now={now} />)}
          </div>
        ) : (
          <p className="note">{t("owner.empty.cases")}</p>
        )}
      </section>
      <div className="split">
        {table("team")}
        {table("suppliers")}
      </div>
    </main>
  );
}

const style = document.createElement("style");
style.textContent = STYLE;
document.head.append(style);
const root = document.getElementById("root");
createRoot(root).render(root.dataset.view === "owner" ? <Owner /> : <Operator />);
