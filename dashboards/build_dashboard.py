"""Builds fraud_monitoring.lvdash.json, the AI/BI dashboard over the gold KPI views.

Run `uv run python dashboards/build_dashboard.py` after editing; the JSON is committed because the
bundle deploys that file. Writing it from Python keeps the repeated widget shapes in one place and
lets tests check the whole thing (tests/test_dashboard.py).

Design (ADR-028): one page, four small datasets (one per KPI view, bare table names, because the
bundle supplies catalog and schema), ratios defined once as dataset measures so every widget
agrees, and a source filter that defaults to the generated feed, because PaySim's daily trend is a
simulation artifact (docs/kpis.md, caveat 1).
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("fraud_monitoring.lvdash.json")

PERCENT = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 2}}
COMPACT = {
    "type": "number",
    "abbreviation": "compact",
    "decimalPlaces": {"type": "max", "places": 1},
}


def ratio(numerator: str, denominator: str) -> str:
    return (
        f"CASE WHEN SUM(`{denominator}`) = 0 THEN NULL "
        f"ELSE SUM(`{numerator}`) * 1.0 / SUM(`{denominator}`) END"
    )


DATASETS = [
    {
        "name": "ds_daily",
        "displayName": "Daily KPIs",
        "queryLines": [
            "SELECT calendar_date, source_system, txn_count, txn_value, approved_count, ",
            "declined_count, fraud_count, fraud_value, fraud_missed_count, fraud_missed_value ",
            "FROM kpi_daily",
        ],
        "columns": [
            {"displayName": "Transactions", "expression": "SUM(`txn_count`)"},
            {"displayName": "Fraud rate", "expression": ratio("fraud_count", "txn_count")},
            {"displayName": "Approval rate", "expression": ratio("approved_count", "txn_count")},
            {"displayName": "Fraud missed value", "expression": "SUM(`fraud_missed_value`)"},
        ],
    },
    {
        "name": "ds_type",
        "displayName": "Fraud by type",
        "queryLines": [
            "SELECT type_name, source_system, txn_count, fraud_count ",
            "FROM kpi_fraud_by_type",
        ],
        "columns": [
            {"displayName": "Fraud rate", "expression": ratio("fraud_count", "txn_count")},
        ],
    },
    {
        "name": "ds_hour",
        "displayName": "Fraud by hour",
        "queryLines": [
            "SELECT hour_of_day, source_system, txn_count, fraud_count ",
            "FROM kpi_fraud_by_hour",
        ],
        "columns": [
            {"displayName": "Fraud rate", "expression": ratio("fraud_count", "txn_count")},
        ],
    },
    {
        "name": "ds_rules",
        "displayName": "Rule effectiveness",
        "queryLines": [
            "SELECT source_system, rule_name, flagged_count, fraud_caught, fraud_total, ",
            "precision_rate, recall_rate ",
            "FROM rule_effectiveness ",
            "ORDER BY rule_name",
        ],
    },
]


def text(name, lines, x, y, w, h):
    return {
        "widget": {"name": name, "multilineTextboxSpec": {"lines": lines}},
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def query(dataset, fields, disaggregated, orders=None):
    body = {
        "datasetName": dataset,
        "fields": [{"name": n, "expression": e} for n, e in fields],
        "disaggregated": disaggregated,
    }
    if orders:
        body["orders"] = orders
    return [{"name": "main_query", "query": body}]


def measure(name):
    return (f"measure({name})", f"MEASURE(`{name}`)")


def counter(name, title, dataset, measure_name, fmt, x, y):
    field = measure(measure_name)[0]
    return {
        "widget": {
            "name": name,
            "queries": query(dataset, [measure(measure_name)], False),
            "spec": {
                "version": 2,
                "widgetType": "counter",
                "encodings": {"value": {"fieldName": field, "displayName": title, "format": fmt}},
                "frame": {"title": title, "showTitle": True},
            },
        },
        "position": {"x": x, "y": y, "width": 3, "height": 3},
    }


def chart(name, kind, title, dataset, x_field, x_scale, y_measure, x, y, orders=None):
    x_name, x_expr = x_field
    return {
        "widget": {
            "name": name,
            "queries": query(dataset, [x_field, measure(y_measure)], False, orders),
            "spec": {
                "version": 3,
                "widgetType": kind,
                "encodings": {
                    "x": {"fieldName": x_name, "scale": {"type": x_scale}},
                    "y": {
                        "fieldName": measure(y_measure)[0],
                        "scale": {"type": "quantitative"},
                        "displayName": y_measure,
                        "format": PERCENT if "rate" in y_measure else COMPACT,
                    },
                },
                "frame": {"title": title, "showTitle": True},
            },
        },
        "position": {"x": x, "y": y, "width": 6, "height": 5},
    }


def filter_widget(name, widget_type, title, field, datasets, x, default=None):
    """One filter bound to the same field in several datasets (the field must exist in each)."""
    queries = [
        {
            "name": f"{ds}_{field}",
            "query": {
                "datasetName": ds,
                "fields": [{"name": field, "expression": f"`{field}`"}],
                "disaggregated": False,
            },
        }
        for ds in datasets
    ]
    spec = {
        "version": 2,
        "widgetType": widget_type,
        "encodings": {
            "fields": [
                {"fieldName": field, "displayName": title, "queryName": f"{ds}_{field}"}
                for ds in datasets
            ]
        },
        "frame": {"showTitle": True, "title": title},
    }
    if default:
        spec["selection"] = {
            "defaultSelection": {"values": {"dataType": "STRING", "values": [{"value": default}]}}
        }
    return {
        "widget": {"name": name, "queries": queries, "spec": spec},
        "position": {"x": x, "y": 2, "width": 4, "height": 2},
    }


def rules_table():
    columns = [
        ("rule_name", "Rule", None),
        ("flagged_count", "Rows flagged", COMPACT),
        ("fraud_caught", "Fraud caught", COMPACT),
        ("fraud_total", "All fraud", COMPACT),
        ("precision_rate", "Precision", PERCENT),
        ("recall_rate", "Recall", PERCENT),
    ]
    encoded = []
    for field, label, fmt in columns:
        column = {"fieldName": field, "displayName": label}
        if fmt:
            column["format"] = fmt
        encoded.append(column)
    return {
        "widget": {
            "name": "rules-table",
            "queries": query("ds_rules", [(c[0], f"`{c[0]}`") for c in columns], True),
            "spec": {
                "version": 2,
                "widgetType": "table",
                "encodings": {"columns": encoded},
                "frame": {"title": "How good are the simple rules?", "showTitle": True},
            },
        },
        "position": {"x": 0, "y": 20, "width": 12, "height": 5},
    }


def layout():
    day = ("daily(calendar_date)", 'DATE_TRUNC("DAY", `calendar_date`)')
    by_hour = [{"direction": "ASC", "expression": "`hour_of_day`"}]
    all_datasets = ["ds_daily", "ds_type", "ds_hour", "ds_rules"]
    return [
        text(
            "title",
            [
                "# Payments fraud monitoring\n",
                "Synthetic payments (PaySim plus a daily generator), processed through bronze, "
                "silver and gold. The default view is the generated feed; switch the source to "
                "see PaySim. Rates are calculated from sums, so they stay correct when filtered.",
            ],
            0,
            0,
            12,
            2,
        ),
        filter_widget(
            "filter-source",
            "filter-single-select",
            "Source",
            "source_system",
            all_datasets,
            0,
            default="generator-v1",
        ),
        filter_widget(
            "filter-date",
            "filter-date-range-picker",
            "Date (daily panels)",
            "calendar_date",
            ["ds_daily"],
            4,
        ),
        text(
            "filter-note",
            [
                "The date filter applies to the KPI tiles and daily charts. The type, hour and "
                "rule panels are all-time, because their gold tables have no date column."
            ],
            8,
            2,
            4,
            2,
        ),
        counter("kpi-transactions", "Transactions", "ds_daily", "Transactions", COMPACT, 0, 4),
        counter("kpi-fraud-rate", "Fraud rate", "ds_daily", "Fraud rate", PERCENT, 3, 4),
        counter("kpi-approval", "Approval rate", "ds_daily", "Approval rate", PERCENT, 6, 4),
        counter(
            "kpi-missed",
            "Fraud approved (missed) value",
            "ds_daily",
            "Fraud missed value",
            COMPACT,
            9,
            4,
        ),
        text("section-trend", ["## Over time"], 0, 7, 12, 1),
        chart(
            "line-volume",
            "line",
            "Daily transactions",
            "ds_daily",
            day,
            "temporal",
            "Transactions",
            0,
            8,
        ),
        chart(
            "line-fraud-rate",
            "line",
            "Daily fraud rate",
            "ds_daily",
            day,
            "temporal",
            "Fraud rate",
            6,
            8,
        ),
        text("section-where", ["## Where the fraud is"], 0, 13, 12, 1),
        chart(
            "bar-type",
            "bar",
            "Fraud rate by transaction type",
            "ds_type",
            ("type_name", "`type_name`"),
            "categorical",
            "Fraud rate",
            0,
            14,
        ),
        chart(
            "bar-hour",
            "bar",
            "Fraud rate by hour of day",
            "ds_hour",
            ("hour_of_day", "`hour_of_day`"),
            "categorical",
            "Fraud rate",
            6,
            14,
            by_hour,
        ),
        text("section-rules", ["## Rules"], 0, 19, 12, 1),
        rules_table(),
        text(
            "caveats",
            [
                "**Read with care.** PaySim's daily fraud rate and its overnight peak are "
                "simulation artifacts. The rule numbers flatter the rules, because the generated "
                "fraud was built from the same patterns they look for. Rows that failed a quality "
                "check are in the quarantine table and are not counted here. Definitions: "
                "docs/kpis.md.",
            ],
            0,
            25,
            12,
            2,
        ),
    ]


def build():
    return {
        "datasets": DATASETS,
        "pages": [
            {
                "name": "overview",
                "displayName": "Fraud monitoring",
                "pageType": "PAGE_TYPE_CANVAS",
                "layoutVersion": "GRID_V1",
                "layout": layout(),
            }
        ],
        "uiSettings": {
            "theme": {
                "canvasBackgroundColor": {"light": "#FCFCFC", "dark": "#1F272D"},
                "widgetBackgroundColor": {"light": "#FFFFFF", "dark": "#11171C"},
                "fontColor": {"light": "#11171C", "dark": "#E8ECF0"},
                "selectionColor": {"light": "#0072B2", "dark": "#8ACAFF"},
                # Okabe-Ito: distinguishable with common colour blindness.
                "visualizationColors": [
                    "#0072B2",
                    "#E69F00",
                    "#009E73",
                    "#CC79A7",
                    "#D55E00",
                    "#56B4E9",
                ],
                "widgetHeaderAlignment": "LEFT",
            }
        },
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT.name}")
