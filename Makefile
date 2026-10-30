.PHONY: check lint test arch dup security docs docs-check i18n db run evals evals-rapido demo
lint: ; uv run ruff check src tests tools && uv run mypy src/vigia/domain src/vigia/signals src/vigia/verdict src/vigia/policy src/vigia/jev src/vigia/analyst src/vigia/model src/vigia/services --strict
arch: ; uv run lint-imports
dup:  ; uv run pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 src/
security: ; uv run bandit -q -r src && uv run pip-audit && uv run pytest -q tests/test_owasp.py
test: ; uv run pytest -q
docs: ; uv run python tools/repo_map.py > docs/MAP.md && uv run pdoc vigia -o docs/api && uv run git-cliff -o CHANGELOG.md
docs-check: ; uv run interrogate -q --fail-under 90 src && uv run python tools/repo_map.py | diff -q - docs/MAP.md && uv run python tools/check_architecture_doc.py
i18n: ; uv run pytest -q tests/test_i18n.py
check: lint arch dup security docs-check i18n test
db: ; docker compose up -d --wait postgres
run: ; docker compose up --build
evals: ; uv run --env-file .env python tools/measure.py --max-usd 2.0
evals-rapido: ; uv run --env-file .env python tools/measure.py --filtro rapido=True --max-usd 1.0
demo: ; docker compose up -d --build api && uv run --env-file .env python tools/demo.py
