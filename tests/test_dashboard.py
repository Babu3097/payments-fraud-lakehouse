"""Tests for the AI/BI dashboard: the committed JSON is what the builder makes, every widget reads
fields that exist, the grid has no overlaps or gaps, and widget versions are the ones Databricks
accepts. `databricks bundle validate` cannot check any of this offline."""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboards"))
import build_dashboard  # noqa: E402

DASHBOARD = json.loads((REPO / "dashboards/fraud_monitoring.lvdash.json").read_text())
DATASETS = {d["name"]: d for d in DASHBOARD["datasets"]}
PAGE = DASHBOARD["pages"][0]
ITEMS = PAGE["layout"]
VERSIONS = {"counter": 2, "table": 2, "bar": 3, "line": 3}
ALIAS = re.compile(r"^SELECT (.*?) FROM", re.DOTALL)


def dataset_columns(name: str) -> set[str]:
    select = ALIAS.match("".join(DATASETS[name]["queryLines"]).strip()).group(1)
    return {c.strip() for c in select.split(",")}


def measures(name: str) -> set[str]:
    return {c["displayName"] for c in DATASETS[name].get("columns", [])}


def widgets(kind=None):
    for item in ITEMS:
        spec = item["widget"].get("spec")
        if spec and (kind is None or spec["widgetType"] == kind):
            yield item["widget"], spec


def test_the_committed_json_is_what_the_builder_produces():
    assert json.loads(json.dumps(build_dashboard.build())) == DASHBOARD  # run the builder if not


def test_the_queries_use_bare_table_names_because_the_bundle_supplies_the_schema():
    for dataset in DATASETS.values():
        sql = "".join(dataset["queryLines"])
        assert not re.search(r"FROM\s+\w+\.\w+", sql), sql


@pytest.mark.parametrize("name", [w["name"] for w, _ in widgets()])
def test_every_field_a_widget_asks_for_exists_in_its_dataset(name):
    widget = next(w for w, _ in widgets() if w["name"] == name)
    for q in widget["queries"]:
        dataset = q["query"]["datasetName"]
        for field in q["query"]["fields"]:
            expression = field["expression"]
            column = re.findall(r"`([^`]+)`", expression)[0]
            allowed = dataset_columns(dataset) | measures(dataset)
            assert column in allowed, f"{name}: {column} is not in {dataset}"


def test_each_encoding_names_a_field_the_widget_queries():
    for widget, spec in widgets():
        queried = {f["name"] for q in widget["queries"] for f in q["query"]["fields"]}
        encodings = spec["encodings"]
        named = set()
        for key, value in encodings.items():
            if key == "columns":
                named |= {c["fieldName"] for c in value}
            elif isinstance(value, dict) and "fieldName" in value:
                named.add(value["fieldName"])
        for field in named:
            assert field in queried, f"{widget['name']} encodes {field}, which it does not query"


def test_widget_versions_match_what_databricks_accepts():
    for widget, spec in widgets():
        if spec["widgetType"] in VERSIONS:
            assert spec["version"] == VERSIONS[spec["widgetType"]], widget["name"]


def test_the_grid_has_no_overlap_and_stays_inside_twelve_columns():
    taken = set()
    for item in ITEMS:
        p = item["position"]
        assert p["x"] + p["width"] <= 12, item["widget"]["name"]
        cells = {
            (x, y)
            for x in range(p["x"], p["x"] + p["width"])
            for y in range(p["y"], p["y"] + p["height"])
        }
        assert not cells & taken, f"{item['widget']['name']} overlaps another widget"
        taken |= cells


def test_there_are_no_empty_gaps_in_the_rows():
    height = max(i["position"]["y"] + i["position"]["height"] for i in ITEMS)
    covered = {
        (x, y)
        for i in ITEMS
        for x in range(i["position"]["x"], i["position"]["x"] + i["position"]["width"])
        for y in range(i["position"]["y"], i["position"]["y"] + i["position"]["height"])
    }
    for y in range(height):
        assert {x for x, row in covered if row == y} == set(range(12)), f"row {y} has a gap"


def test_the_source_filter_reaches_every_dataset_and_defaults_to_the_generated_feed():
    source = next(w for w, s in widgets() if w["name"] == "filter-source")
    reached = {q["query"]["datasetName"] for q in source["queries"]}
    assert reached == set(DATASETS)
    spec = source["spec"]["selection"]["defaultSelection"]["values"]["values"]
    assert spec == [{"value": "generator-v1"}]


def test_every_dataset_a_filter_reaches_has_that_column():
    for widget, spec in widgets():
        if not spec["widgetType"].startswith("filter-"):
            continue
        for q in widget["queries"]:
            field = q["query"]["fields"][0]["name"]
            assert field in dataset_columns(q["query"]["datasetName"]), widget["name"]


def test_the_page_has_the_grid_version_databricks_requires():
    assert PAGE["layoutVersion"] == "GRID_V1" and PAGE["pageType"] == "PAGE_TYPE_CANVAS"


def test_the_bundle_deploys_the_committed_file_and_commits_no_warehouse_id():
    resource = yaml.safe_load((REPO / "resources/fraud_monitoring.dashboard.yml").read_text())
    dashboard = resource["resources"]["dashboards"]["fraud_monitoring"]
    assert (REPO / "resources" / dashboard["file_path"]).resolve().exists()
    assert dashboard["warehouse_id"] == "${var.warehouse_id}"
    assert dashboard["dataset_schema"] == "gold"
