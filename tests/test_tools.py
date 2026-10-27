import check_architecture_doc
import repo_map


def test_map_lists_every_module_with_its_layer() -> None:
    lines = repo_map.render().splitlines()

    assert any("`vigia.store.migrate`" in line and "adapters" in line for line in lines)
    assert any("`vigia.domain`" in line and "domain" in line for line in lines)


def test_architecture_check_reports_packages_missing_from_the_diagram() -> None:
    doc = "```mermaid\ngraph TD\n  vigia.app --> vigia.store\n```"

    missing = check_architecture_doc.missing_packages(doc, ["app", "store", "signals"])

    assert missing == ["signals"]
