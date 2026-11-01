// The two React views of the panel (D-121): the operator's, behind the operator token, and
// the owner's, behind the session cookie from /entrar. The server picks one in #root.
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
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

const VERDICT_COLUMNS = [
  { key: "label", title: t("panel.col.label"), lead: true, cell: (r) => <Tag label={r.label} /> },
  { key: "brand", title: t("panel.col.brand"), lead: true, cell: (r) => r.brand || "" },
  { key: "time", title: t("panel.col.time"), cell: (r) => when(r.created_at) },
  { key: "kind", title: t("panel.col.kind"), cell: (r) => r.kind || "" },
  { key: "confidence", title: t("panel.col.confidence"), cell: (r) => fixed(2)(r.confidence) },
  {
    key: "signals",
    title: t("panel.col.signals"),
    cell: (r) =>
      r.signal_types.length ? <div className="chips"><Chips names={r.signal_types} kind="sig" /></div> : "",
  },
  {
    key: "tools",
    title: t("panel.col.tools"),
    cell: (r) =>
      r.tools.length || r.alert_rejections.length ? (
        <div className="chips">
          <Chips names={r.tools} kind="tool" />
          <Chips names={r.alert_rejections} kind="warn" />
        </div>
      ) : "",
  },
  { key: "usd", title: t("panel.col.usd"), cell: (r) => fixed(4)(r.usd) },
  { key: "ms", title: t("panel.col.ms"), cell: (r) => String(r.latency_ms) },
];

function Today({ today }) {
  return (
    <div className="today">
      {t("panel.today")}
      {["scam", "legit", "doubtful"].map((label) => (
        <div key={label} className="card">
          {t(`verdict.label.${label}`)}
          <b>{today[label] || 0}</b>
        </div>
      ))}
    </div>
  );
}

function Try({ rulesOnly, onTried }) {
  const [text, setText] = useState("");
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
      <form onSubmit={submit}>
        <textarea name="text" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="actions">
          <label>
            {t("panel.try.file")}
            <input type="file" name="file" accept="audio/*,image/*,application/pdf" />
          </label>
          <button className={`primary${busy ? " busy" : ""}`} type="submit" disabled={busy}>
            {busy ? t("panel.try.busy") : t("panel.try.button")}
          </button>
        </div>
        <p className="note">{t("panel.try.wait")}</p>
      </form>
      <div className="examples">
        <span>{t("panel.try.examples")}</span>
        {EXAMPLES.map((name) => (
          <button key={name} className="quick" type="button" disabled={busy} onClick={() => example(name)}>
            {t(`panel.try.example.${name}`)}
          </button>
        ))}
      </div>
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
            <Rows columns={VERDICT_COLUMNS} rows={[tried.row]} />
          </div>
        </div>
      )}
    </section>
  );
}

function Metrics({ result }) {
  if (!result) return <p className="note">{t("panel.no_metrics")}</p>;
  const scalars = Object.entries(result.metricas || {}).filter(([, v]) =>
    ["number", "string"].includes(typeof v),
  );
  const show = (v) => (typeof v === "number" && !Number.isInteger(v) ? v.toFixed(3) : String(v));
  return (
    <section className="metrics">
      <h2>
        {t("panel.metrics")}
        {result.fecha ? ` · ${result.fecha}` : ""}
      </h2>
      <div className="cards">
        {scalars.map(([name, value]) => (
          <div key={name} className="card">
            {has(`metric.${name}`) ? t(`metric.${name}`) : name}
            <b>{show(value)}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function Operator() {
  const [{ data, failed }, reload] = useApi("/api/operator");
  return (
    <main>
      <header className="top">
        <h1>{t("panel.title")}</h1>
        {data && <Today today={data.today} />}
      </header>
      <Try rulesOnly={data?.rules_only} onTried={reload} />
      {failed && <p className="error" role="alert">{t("panel.try.error")}</p>}
      {!data && !failed && <p className="note">{t("owner.loading")}</p>}
      {data && (
        <>
          <section>
            <h2>{t("panel.verdicts")}</h2>
            <div className="frame">
              <Rows columns={VERDICT_COLUMNS} rows={data.rows} empty={t("panel.empty")} />
            </div>
          </section>
          <Metrics result={data.metrics} />
        </>
      )}
    </main>
  );
}

const kind = (k) => (k && has(`owner.kind.${k}`) ? t(`owner.kind.${k}`) : k || "");

const OWNER_TABLES = {
  cases: [
    { key: "label", title: t("owner.col.label"), lead: true, cell: (c) => <Tag label={c.label} /> },
    { key: "brand", title: t("owner.col.brand"), lead: true, cell: (c) => c.brand || "" },
    { key: "when", title: t("owner.col.when"), cell: (c) => when(c.at) },
    { key: "case", title: t("owner.col.case"), cell: (c) => (c.ref == null ? "" : String(c.ref)) },
    { key: "kind", title: t("owner.col.kind"), cell: (c) => kind(c.kind) },
  ],
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

function Owner() {
  const [{ data, failed }] = useApi("/api/owner");
  if (failed) return <main><p className="error" role="alert">{t("owner.error")}</p></main>;
  if (!data) return <main><p className="note">{t("owner.loading")}</p></main>;
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
    <main>
      <header className="top">
        <h1>{document.title}</h1>
      </header>
      {table("cases")}
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
