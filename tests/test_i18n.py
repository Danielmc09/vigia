"""User-facing text lives only in src/vigia/i18n (specs/13)."""

import ast
import json
import re
from pathlib import Path

import pytest

from vigia.i18n import t

SRC = Path(__file__).parents[1] / "src" / "vigia"
I18N = SRC / "i18n"
_SPANISH_LETTERS = re.compile(r"[áéíóúüñÁÉÍÓÚÜÑ¿¡]")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_USER_WORDS = re.compile(
    r"\b(estafa|leg[ií]timo|dudoso|mensaje|hola|gracias|por favor|usted|cuidado"
    r"|scam|legitimate|unclear|suspicious|hello|thanks|please)\b",
    re.I,
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _literal_type_values(tree: ast.AST) -> set[int]:
    """Identifier-like values of ``Literal[...]`` (``"scam"``), which are not user text."""
    return {
        id(const)
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Literal"
        for const in ast.walk(node.slice)
        if isinstance(const, ast.Constant)
        and isinstance(const.value, str)
        and _IDENTIFIER.fullmatch(const.value)
    }


def _user_literals(source: str) -> list[str]:
    tree = ast.parse(source)
    docs = _docstring_nodes(tree) | _literal_type_values(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and (_SPANISH_LETTERS.search(node.value) or _USER_WORDS.search(node.value))
    ]


def test_detector_flags_user_text_but_not_docstrings() -> None:
    source = (
        '"""Module doc about a scam (estafa)."""\n'
        'x = "Esto es una estafa"\ny = "acción"\nz = "This is a scam"\nw = "status"\n'
        'Label = Literal["scam", "legit"]\nBad = Literal["Esto es una estafa"]\n'
    )

    assert sorted(_user_literals(source)) == sorted(
        ["Esto es una estafa", "acción", "This is a scam", "Esto es una estafa"]
    )


def test_no_user_text_outside_i18n() -> None:
    # Second copy of the "scan every src module" loop (also in test_owasp.py); rule of
    # three: extract it on the third use.
    offenders = {
        str(path.relative_to(SRC)): found
        for path in SRC.rglob("*.py")
        if I18N not in path.parents and (found := _user_literals(path.read_text("utf-8")))
    }

    assert offenders == {}


def test_every_language_has_the_same_keys() -> None:
    es = json.loads((I18N / "es.json").read_text("utf-8"))
    en = json.loads((I18N / "en.json").read_text("utf-8"))

    assert es.keys() == en.keys()
    assert es  # at least the first keys exist


def test_translate_returns_text_in_the_business_language() -> None:
    assert t("es", "verdict.scam.header") != t("en", "verdict.scam.header")


def test_translate_fills_parameters() -> None:
    assert "Bancolombia" in t("es", "verdict.scam.brand", brand="Bancolombia")


def test_translate_rejects_unknown_language() -> None:
    with pytest.raises(ValueError):
        t("../secrets", "verdict.scam.header")
