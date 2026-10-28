"""Validate evals/casos.jsonl against the case schema of specs/06.

Usage: ``python tools/validate_cases.py [path]`` (default ``evals/casos.jsonl``).
Exit 0 when valid, 1 with one line per problem on stderr otherwise.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, get_args

from vigia.domain.eval_case import Category, EvalKind, EvalOrigin, Expected
from vigia.domain.knowledge import KnownCampaign, simhash
from vigia.signals.country_pack import COUNTRIES_DIR, country_pack
from vigia.signals.knowledge import load_campaigns, text_distance

REPO = Path(__file__).parents[1]
DEFAULT = REPO / "evals" / "casos.jsonl"
KNOWLEDGE = REPO / "conocimiento" / "semilla.jsonl"

REQUIRED = ("id", "tipo", "pais", "esperado", "categoria", "hipotesis", "autor", "fecha", "origen")
CHOICES = {
    "tipo": set(get_args(EvalKind)),
    "esperado": set(get_args(Expected)),
    "origen": set(get_args(EvalOrigin)),
    "categoria": set(get_args(Category)),
}
MIN_LEGIT_SHARE = 0.35  # specs/06: ">= 35% legitimos"


def load_brands() -> dict[str, set[str]]:
    """Brand names per country ISO code, read with the same loader the signals use."""
    packs = {p.stem: country_pack(p.stem) for p in COUNTRIES_DIR.glob("*.json")}
    return {iso: {b.name for b in pack.brands} for iso, pack in packs.items() if pack}


def _field_errors(case: dict[str, Any]) -> list[str]:
    errors = [f"missing {f}" for f in REQUIRED if not str(case.get(f) or "").strip()]
    if not (case.get("texto") or case.get("fuente")):
        errors.append("needs texto or fuente")
    errors += [
        f"invalid {f}={case[f]!r}" for f, ok in CHOICES.items() if case.get(f) and case[f] not in ok
    ]
    try:
        date.fromisoformat(str(case.get("fecha")))
    except ValueError:
        errors.append(f"invalid fecha={case.get('fecha')!r}")
    return errors


def _consistency_errors(case: dict[str, Any], brands: dict[str, set[str]], root: Path) -> list[str]:
    errors = []
    country = case.get("pais")
    if country not in brands:
        errors.append(f"no country pack for pais={country!r}")
    elif case.get("marca") and case["marca"] not in brands[country]:
        errors.append(f"marca {case['marca']!r} not in {country} pack")
    if case.get("esperado") == "dudoso" and not case.get("ambiguo"):
        errors.append("esperado=dudoso requires ambiguo")
    category = str(case.get("categoria") or "")
    if category.startswith("legitimo_") and case.get("esperado") not in ("legitimo", "dudoso"):
        errors.append(f"categoria {category} requires esperado=legitimo or dudoso")
    if case.get("fuente") and not (root / case["fuente"]).is_file():
        errors.append(f"fuente not found: {case['fuente']}")
    if case.get("kb_test") not in (None, True, False):
        errors.append(f"invalid kb_test={case['kb_test']!r}")
    return errors


def _knowledge_errors(case: dict[str, Any], campaigns: tuple[KnownCampaign, ...]) -> list[str]:
    """specs/10 §4 hygiene: an eval that repeats a known campaign measures memory, not skill."""
    fingerprint = simhash(str(case.get("texto") or ""))
    if case.get("kb_test") is True or fingerprint is None:
        return []
    hits = ((c, text_distance(c, fingerprint)) for c in campaigns)
    return [
        f"collides with {c.id} (simhash distance {d}); reword it or mark kb_test: true"
        for c, d in hits
        if d is not None
    ]


def validate_file(path: Path, root: Path = REPO, knowledge: Path = KNOWLEDGE) -> list[str]:
    """All problems found in a JSONL eval set; an empty list means it is valid.

    Args:
        path: The eval set.
        root: Directory that ``fuente`` paths are relative to.
        knowledge: Known-campaign base the cases must not collide with (absent = empty).
    """
    brands = load_brands()
    campaigns = load_campaigns(knowledge)
    errors: list[str] = []
    seen: set[str] = set()
    cases = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {number}: invalid JSON ({exc.msg})")
            continue
        case_id = case.get("id") or f"line {number}"
        problems = _field_errors(case) + _consistency_errors(case, brands, root)
        problems += _knowledge_errors(case, campaigns)
        if case_id in seen:
            problems.append("duplicate id")
        seen.add(case_id)
        errors += [f"{case_id}: {p}" for p in problems]
        cases.append(case)
    share = sum(c.get("esperado") == "legitimo" for c in cases) / max(len(cases), 1)
    if share < MIN_LEGIT_SHARE:
        errors.append(f"legitimate share {share:.0%} < {MIN_LEGIT_SHARE:.0%} (specs/06)")
    return errors


def main(argv: list[str]) -> int:
    """Validate the file given in ``argv`` (or the default set) and report on stderr."""
    path = Path(argv[0]) if argv else DEFAULT
    errors = validate_file(path)
    for error in errors:
        print(error, file=sys.stderr)
    print(f"{path}: {'INVALID' if errors else 'ok'} ({len(errors)} problems)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
