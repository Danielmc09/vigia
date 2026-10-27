"""Run an eval set against VIGIA and grade it deterministically (specs/06).

Usage: ``python medir.py [evals/casos.jsonl] [--filtro campo=valor] [--solo-deterministas]
[--json out.json] [--max-usd 2.0] [--adversarios]``.

Exit codes: 0 ok, 1 gate or regression, 2 over budget, 3 mode not available yet.
"""

import argparse
import json
import mimetypes
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from vigia.domain.case import Case, IncomingMessage
from vigia.domain.eval_case import CHANNEL_KIND, EXPECTED_LABEL, Expected
from vigia.domain.member import Business
from vigia.domain.verdict import Label
from vigia.services.ingest import normalize
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.verdict.fusion import fuse

REPO = Path(__file__).parents[1]
EVALS = REPO / "evals"
EVAL_BUSINESS = uuid5(NAMESPACE_URL, "vigia:eval")

GATE_MIN = {"recall_estafa": 0.90}
GATE_MAX = {"fp_legitimo": 0.10, "dudosos": 0.25}
HIGHER_IS_BETTER = ("exactitud", "recall_estafa")
LOWER_IS_BETTER = ("fp_legitimo", "dudosos")
MAX_DROP = 0.02  # specs/06: no metric may fall more than 2 points
EXIT_OK, EXIT_GATE, EXIT_BUDGET, EXIT_UNAVAILABLE = 0, 1, 2, 3

EXPECTED_OF: dict[Label, Expected] = {label: expected for expected, label in EXPECTED_LABEL.items()}


@dataclass(frozen=True)
class Outcome:
    """What the system answered for one case, plus what it cost.

    Attributes:
        label: Verdict label.
        confidence: Verdict confidence.
        signals: Names of the signals that fired.
        tools_used: Analyst tool calls (name, ms, usd).
        usd: Model spend for the case.
        tokens: Model tokens for the case.
        degraded: True when the verdict came without the analyst.
    """

    # ponytail: stands in for the Verdict of specs/05 until fusion exists (T-07).
    label: Label
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    tools_used: list[dict[str, Any]] = field(default_factory=list)
    usd: float = 0.0
    tokens: int = 0
    degraded: bool = False


Classifier = Callable[[Case], Outcome]
"""Anything that turns a Case into an Outcome: the deterministic path or the app."""


def deterministic(case: Case) -> Outcome:
    """Verdict from code alone: signals of specs/04 fused by specs/05, no model, no network.

    Args:
        case: The case to classify.

    Returns:
        The code-only outcome; it never spends and is always degraded (no analyst).
    """
    pack = country_pack(case.country)
    signals = detect(case, pack) if pack else []
    label, confidence = fuse(signals)
    return Outcome(
        label=label, confidence=confidence, signals=[s.type for s in signals], degraded=True
    )


def to_case(raw: dict[str, Any], root: Path = REPO) -> Case:
    """Build the Case the system would receive for one eval record.

    The record goes through the same :func:`normalize` as a channel message, so a
    lone link is a ``url`` case and an email carries its headers.

    Args:
        raw: One line of the eval set.
        root: Directory that ``fuente`` paths are relative to.

    Returns:
        The normalized Case, from the eval origin and a fixed eval business.

    Raises:
        ValueError: If ingestion does not support the record's type.
    """
    source = root / raw["fuente"] if raw.get("fuente") else None
    msg = IncomingMessage(
        message_id=raw["id"],
        origin="local",
        sender="eval",
        kind=CHANNEL_KIND[raw["tipo"]],
        text=raw.get("texto"),
        attachment_ref=str(source) if source else None,
        mime=mimetypes.guess_type(source)[0] if source else None,
        eml_bytes=source.read_bytes() if source and source.suffix == ".eml" else None,
        received_at=datetime.fromisoformat(raw["fecha"]).replace(tzinfo=UTC),
    )
    case = normalize(msg, Business(EVAL_BUSINESS, raw["pais"], "es"))
    if case is None:
        raise ValueError(f"{raw['id']}: ingest does not support tipo={raw['tipo']}")
    return case.model_copy(
        update={
            "id": uuid5(NAMESPACE_URL, f"vigia:eval:{raw['id']}"),
            "origin": "eval",
            "sender": None,
            "context": raw.get("contexto") or {},
        }
    )


def _ask(
    raw: dict[str, Any], classify: Classifier, root: Path
) -> tuple[Outcome | None, str | None]:
    """Classify one record; a missing attachment or a timeout is a failed case, not a crash."""
    if raw.get("fuente") and not (root / raw["fuente"]).is_file():
        return None, f"attachment not found: {raw['fuente']}"
    try:
        return classify(to_case(raw, root)), None
    except TimeoutError:
        return None, "timeout"


def grade(raw: dict[str, Any], classify: Classifier, root: Path = REPO) -> dict[str, Any]:
    """Run one eval record and grade it: pass only when the label matches exactly.

    Args:
        raw: One line of the eval set.
        classify: The system under test.
        root: Directory that ``fuente`` paths are relative to.

    Returns:
        The per-case record of specs/06 (Spanish keys, as the result file is data).
    """
    start = time.perf_counter()
    outcome, error = _ask(raw, classify, root)
    obtained = EXPECTED_OF[outcome.label] if outcome else None
    passed = obtained == raw["esperado"]
    return {
        **{k: raw.get(k) for k in ("id", "esperado", "tipo", "categoria", "pais")},
        "obtenido": obtained,
        "paso": passed,
        # ponytail: hypotheses are free text; "confirmed" means the predicted label came out.
        "hipotesis_confirmada": passed,
        "confianza": outcome.confidence if outcome else None,
        "senales": outcome.signals if outcome else [],
        "herramientas_usadas": outcome.tools_used if outcome else [],
        "degradado": outcome.degraded if outcome else False,
        "ms": round((time.perf_counter() - start) * 1000, 1),
        "usd": outcome.usd if outcome else 0.0,
        "tokens": outcome.tokens if outcome else 0,
        "error": error,
    }


def _ratio(hits: int, total: int) -> float | None:
    return hits / total if total else None


def _accuracy_by(records: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    groups: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        groups[record[key]].append(record["paso"])
    return {name: _ratio(sum(passes), len(passes)) for name, passes in groups.items()}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics of specs/06 over graded records; a ratio with no cases is ``None``.

    Args:
        records: Output of :func:`grade` for each case.

    Returns:
        Global, per-category, per-type and per-country metrics, cost and latency.
    """
    scams = [r for r in records if r["esperado"] == "estafa"]
    legits = [r for r in records if r["esperado"] == "legitimo"]
    answered = [r["ms"] for r in records if r["error"] is None]
    return {
        "casos": len(records),
        "exactitud": _ratio(sum(r["paso"] for r in records), len(records)),
        "recall_estafa": _ratio(sum(r["obtenido"] == "estafa" for r in scams), len(scams)),
        "fp_legitimo": _ratio(sum(r["obtenido"] == "estafa" for r in legits), len(legits)),
        "dudosos": _ratio(sum(r["obtenido"] == "dudoso" for r in records), len(records)),
        "por_categoria": _accuracy_by(records, "categoria"),
        "por_tipo": _accuracy_by(records, "tipo"),
        "por_pais": _accuracy_by(records, "pais"),
        "mediana_ms": median(answered) if answered else None,
        "usd_total": sum(r["usd"] for r in records),
        "tokens_total": sum(r["tokens"] for r in records),
        "degradados": sum(r["degradado"] for r in records),
        "hipotesis_confirmadas": sum(r["hipotesis_confirmada"] for r in records),
    }


def gate_failures(metrics: dict[str, Any]) -> list[str]:
    """Metrics outside the thresholds of specs/06."""
    low = [k for k, floor in GATE_MIN.items() if (metrics[k] or 0.0) < floor]
    high = [k for k, ceiling in GATE_MAX.items() if (metrics[k] or 0.0) > ceiling]
    return low + high


def regressions(metrics: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Metrics that got more than :data:`MAX_DROP` worse than the baseline run."""
    worse = {k: 1 for k in HIGHER_IS_BETTER} | {k: -1 for k in LOWER_IS_BETTER}
    return [
        k
        for k, sign in worse.items()
        if metrics.get(k) is not None
        and baseline.get(k) is not None
        and sign * (baseline[k] - metrics[k]) > MAX_DROP + 1e-9
    ]


def run(
    cases: list[dict[str, Any]], classify: Classifier, max_usd: float
) -> tuple[list[dict[str, Any]], bool]:
    """Grade cases in order, stopping as soon as spend passes ``max_usd`` (rule 11).

    Returns:
        The graded records and whether the budget was exceeded.
    """
    records: list[dict[str, Any]] = []
    for raw in cases:
        records.append(grade(raw, classify))
        if sum(r["usd"] for r in records) > max_usd:
            return records, True
    return records, False


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="medir.py", description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=EVALS / "casos.jsonl")
    parser.add_argument("--filtro", help="campo=valor, e.g. tipo=audio")
    parser.add_argument("--solo-deterministas", action="store_true")
    parser.add_argument("--adversarios", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--max-usd", type=float, default=2.0)
    return parser.parse_args(argv)


def _load(path: Path, where: str | None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not where:
        return cases
    key, _, value = where.partition("=")
    return [c for c in cases if str(c.get(key)) == value]


def _unavailable(args: argparse.Namespace, out_dir: Path) -> str:
    """Why the requested mode cannot run yet."""
    if args.adversarios:
        adversarial = out_dir / "adversarios.jsonl"
        if not adversarial.is_file():
            return f"{adversarial} not found; the adversarial set arrives with T-08 (D-029)"
        return "adversarial runner not implemented yet (T-08, D-029)"
    return "full mode needs the app (/caso, T-07); use --solo-deterministas"


def _write(result: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _save(result: dict[str, Any], code: int, json_out: Path | None, out_dir: Path) -> None:
    """Dated result always; baseline only from a clean, unfiltered run (D-029)."""
    if json_out:
        _write(result, json_out)
    if result["filtro"]:
        return
    suffix = ".deterministas" if result["modo"] == "deterministas" else ""
    _write(result, out_dir / f"{result['fecha']}{suffix}.resultado.json")
    if code == EXIT_OK:
        _write(result, out_dir / f"ultimo{suffix}.resultado.json")


def _report(result: dict[str, Any]) -> None:
    """Print the summary table and the failed cases."""
    metrics = result["metricas"]
    print(f"mode={result['modo']} filter={result['filtro']} cases={metrics['casos']}")
    for key, value in metrics.items():
        if not isinstance(value, dict):
            shown = f"{value:.3f}" if isinstance(value, float) else value
            print(f"  {key:<22} {shown}")
    for key in ("por_tipo", "por_pais", "por_categoria"):
        print(f"  {key:<22} " + " ".join(f"{k}={v:.2f}" for k, v in metrics[key].items()))
    for case in result["casos"]:
        if not case["paso"]:
            got = case["obtenido"]
            print(f"  FAIL {case['id']} {case['esperado']} -> {got} {case['error'] or ''}")
    gate = "off" if result["gate"] is None else result["gate"] or "ok"
    print(f"gate={gate} regressions={result['regresiones'] or 'none'}")


def main(argv: list[str], classify: Classifier | None = None, out_dir: Path = EVALS) -> int:
    """Run the eval set and return the exit code of specs/06.

    Args:
        argv: Command-line arguments.
        classify: System under test; defaults to :func:`deterministic` with
            ``--solo-deterministas``. Tests inject a fake app here.
        out_dir: Where result files and baselines live.

    Returns:
        0 ok, 1 gate or regression, 2 over budget, 3 mode not available yet.
    """
    args = _parse(argv)
    mode = "deterministas" if args.solo_deterministas else "completo"
    classify = classify or (deterministic if args.solo_deterministas else None)
    if args.adversarios or classify is None:
        print(f"medir: {_unavailable(args, out_dir)}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    records, over_budget = run(_load(args.path, args.filtro), classify, args.max_usd)
    metrics = summarize(records)
    failures = gate_failures(metrics) if mode == "completo" else None
    suffix = ".deterministas" if mode == "deterministas" else ""
    baseline = out_dir / f"ultimo{suffix}.resultado.json"
    previous = json.loads(baseline.read_text("utf-8"))["metricas"] if baseline.is_file() else {}
    regressed = [] if args.filtro else regressions(metrics, previous)
    code = EXIT_BUDGET if over_budget else EXIT_GATE if failures or regressed else EXIT_OK
    result = {
        "fecha": date.today().isoformat(),
        "modo": mode,
        "filtro": args.filtro,
        "metricas": metrics,
        "gate": failures,
        "regresiones": regressed,
        "casos": records,
    }
    _save(result, code, args.json, out_dir)
    _report(result)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
