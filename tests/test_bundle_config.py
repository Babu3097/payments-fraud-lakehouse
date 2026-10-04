"""Tests for the Asset Bundle YAML: the job, the pipeline and the targets.

`databricks bundle validate` needs a workspace login, so it cannot run in public CI. These tests
check the things that matter offline: the task graph is sound, the entry points exist, the alert
address is a variable and never a literal, and the pipeline's folders are all covered.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
BUNDLE = yaml.safe_load((REPO / "databricks.yml").read_text())
JOB = yaml.safe_load((REPO / "resources/daily.job.yml").read_text())["resources"]["jobs"][
    "payments_lakehouse_daily"
]
PIPELINES = yaml.safe_load((REPO / "resources/payments_pipeline.pipeline.yml").read_text())[
    "resources"
]["pipelines"]
PIPELINE = PIPELINES["payments_pipeline"]
PROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
TASKS = {task["task_key"]: task for task in JOB["tasks"]}
VARIABLE = re.compile(r"^\$\{var\.\w+\}$")


def upstream(task_key: str) -> set[str]:
    return {d["task_key"] for d in TASKS[task_key].get("depends_on", [])}


# ---- the task graph ----


def test_every_dependency_names_a_task_that_exists():
    for key in TASKS:
        assert upstream(key) <= set(TASKS), key


def test_the_graph_has_no_cycle():
    done: set[str] = set()
    remaining = set(TASKS)
    while remaining:
        ready = {k for k in remaining if upstream(k) <= done}
        assert ready, f"a cycle among {sorted(remaining)}"
        done |= ready
        remaining -= ready


def test_the_order_is_inputs_then_pipeline_then_verify():
    assert set(TASKS) == {"generate_daily_files", "pull_holidays", "refresh_pipeline", "verify"}
    assert upstream("generate_daily_files") == upstream("pull_holidays") == set()
    assert upstream("refresh_pipeline") == {"generate_daily_files", "pull_holidays"}
    assert upstream("verify") == {"refresh_pipeline"}
    assert not any("verify" in upstream(k) for k in TASKS)  # nothing runs after the checks


# ---- the tasks ----


def test_every_wheel_task_runs_an_entry_point_that_the_package_defines():
    scripts = set(PROJECT["scripts"])
    package = PROJECT["name"].replace("-", "_")
    for key, task in TASKS.items():
        if "python_wheel_task" in task:
            wheel = task["python_wheel_task"]
            assert wheel["entry_point"] in scripts, key
            assert wheel["package_name"] == package, key


def test_every_wheel_task_uses_an_environment_that_installs_the_built_wheel():
    environments = {e["environment_key"]: e for e in JOB["environments"]}
    for key, task in TASKS.items():
        if "python_wheel_task" in task:
            assert task["environment_key"] in environments, key
    for environment in environments.values():
        assert environment["spec"]["dependencies"] == ["../dist/*.whl"]
    artifact = BUNDLE["artifacts"]["payments_lakehouse_wheel"]
    assert artifact["type"] == "whl" and "build" in artifact["build"]


def test_the_pipeline_task_runs_the_pipeline_this_bundle_defines():
    reference = TASKS["refresh_pipeline"]["pipeline_task"]["pipeline_id"]
    assert reference == "${resources.pipelines.payments_pipeline.id}"
    assert "payments_pipeline" in PIPELINES


def test_the_day_comes_from_a_job_parameter_and_the_run_start_date():
    parameters = {p["name"]: p["default"] for p in JOB["parameters"]}
    assert parameters == {"run_date": "yesterday"}
    arguments = TASKS["generate_daily_files"]["python_wheel_task"]["parameters"]
    assert arguments[arguments.index("--date") + 1] == "{{job.parameters.run_date}}"
    assert arguments[arguments.index("--today") + 1] == "{{job.start_time.iso_date}}"


def test_verify_reads_the_check_files_that_the_bundle_deploys():
    arguments = TASKS["verify"]["python_wheel_task"]["parameters"]
    assert arguments[arguments.index("--checks-dir") + 1] == "${workspace.file_path}/sql/checks"
    assert len(list((REPO / "sql/checks").glob("*_reconciliation.sql"))) >= 3


def test_only_the_pipeline_has_an_explicit_retry_and_it_waits_before_trying_again():
    retrying = {k: t for k, t in TASKS.items() if "max_retries" in t}
    assert set(retrying) == {"refresh_pipeline"}
    assert retrying["refresh_pipeline"]["max_retries"] == 1
    assert retrying["refresh_pipeline"]["min_retry_interval_millis"] >= 60_000


# ---- the schedule, the limits and the alerts ----


def test_the_schedule_is_daily_in_london_time_and_active():
    schedule = JOB["schedule"]
    assert schedule["timezone_id"] == "Europe/London"
    assert schedule["pause_status"] == "UNPAUSED"
    assert len(schedule["quartz_cron_expression"].split()) == 6  # a Quartz expression has 6 fields


def test_one_run_at_a_time_and_a_second_waits_instead_of_being_skipped():
    assert JOB["max_concurrent_runs"] == 1
    assert JOB["queue"] == {"enabled": True}


def test_the_warning_comes_before_the_timeout():
    (rule,) = JOB["health"]["rules"]
    assert rule["metric"] == "RUN_DURATION_SECONDS"
    assert 0 < rule["value"] < JOB["timeout_seconds"]


def test_every_alert_recipient_is_a_variable_and_never_a_literal_address():
    notifications = JOB["email_notifications"]
    assert set(notifications) == {"on_failure", "on_duration_warning_threshold_exceeded"}
    for recipients in notifications.values():
        assert recipients and all(VARIABLE.match(r) for r in recipients)
    default = BUNDLE["variables"]["alert_email"]["default"]
    assert default == "${workspace.current_user.userName}"


# ---- the pipeline ----


def test_the_pipeline_is_serverless_triggered_and_pinned_to_utc():
    assert PIPELINE["serverless"] is True and PIPELINE["continuous"] is False
    assert PIPELINE["configuration"]["spark.sql.session.timeZone"] == "UTC"


def test_every_folder_of_pipeline_code_is_included_with_the_one_pattern_the_api_accepts():
    # The API accepts only a plain `**` (ADR-009). A new folder of code that is not listed here
    # would simply never run, with no error, so the list is compared with the folders on disk.
    includes = [lib["glob"]["include"] for lib in PIPELINE["libraries"]]
    assert all(re.fullmatch(r"\.\./pipelines/\w+/\*\*", i) for i in includes), includes
    listed = {i.split("/")[2] for i in includes}
    on_disk = {
        p.parent.name for p in (REPO / "pipelines").glob("*/*") if p.suffix in {".py", ".sql"}
    }
    assert listed == on_disk


# ---- the targets ----


def test_dev_is_deployed_in_development_mode_and_prod_is_defined_as_production():
    targets = BUNDLE["targets"]
    assert targets["dev"]["mode"] == "development" and targets["dev"]["default"] is True
    assert targets["prod"]["mode"] == "production"
    assert "root_path" in targets["prod"]["workspace"]


def test_no_workspace_address_or_email_is_committed_in_any_bundle_file():
    for path in [REPO / "databricks.yml", *sorted((REPO / "resources").glob("*.yml"))]:
        text = path.read_text()
        assert "host:" not in text, f"{path.name} names a workspace host"
        assert not re.search(r"cloud\.databricks\.com", text), path.name
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), f"{path.name} has an email address"


@pytest.mark.parametrize("name", ["catalog", "alert_email"])
def test_the_variables_the_resources_use_are_declared(name):
    assert name in BUNDLE["variables"]
