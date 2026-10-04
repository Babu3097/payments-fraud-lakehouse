# The daily job, and what it would look like in Airflow

This project orchestrates with a Databricks Job (see ADR-021). Many teams use Apache Airflow
instead, so this page maps one onto the other, using this project's four tasks as the example.
Airflow is **not** part of the project: Free Edition has nowhere to host it, and everything here
runs on Databricks, so a Databricks Job is the smaller choice.

## Why a Databricks Job here

- Everything the job touches is on Databricks, so the scheduler sits next to the work and can see
  the state of the pipeline. There is nothing to host, patch or monitor.
- The job is deployed by the same bundle as the pipeline, from the same repository.
- Airflow earns its place when a workflow spans systems (Databricks, a warehouse, a SaaS API, a
  file drop), when a platform team already runs it, or when you need its backfill and sensor tools.

## Concept map

| This project (Databricks Job and bundle) | Airflow |
|---|---|
| Job `payments_lakehouse_daily` | A DAG with the same id |
| Task (`python_wheel_task`, `pipeline_task`) | A task, which is an instance of an operator |
| `depends_on` | `generate >> refresh` |
| `schedule` with a cron expression and `timezone_id` | `schedule="0 6 * * *"` and a timezone-aware `start_date` |
| Job parameter `run_date`, and `--today {{job.start_time.iso_date}}` | The logical date, `{{ ds }}`, fixed for each DAG run |
| `max_concurrent_runs: 1` with `queue` | `max_active_runs=1` |
| `max_retries`, `min_retry_interval_millis` | `retries`, `retry_delay` |
| `timeout_seconds` | `dagrun_timeout`, or `execution_timeout` on a task |
| `email_notifications.on_failure` | `email_on_failure` and `email` (SMTP must be configured), or `on_failure_callback` |
| Duration warning (a health rule) | An SLA or a deadline alert, depending on the version |
| Repair run | Clear the failed task and its downstream tasks, and the scheduler runs them again |
| Run now with parameters | Trigger the DAG with a config |
| `databricks bundle deploy` | Merge the DAG file, then sync it to the scheduler's dags folder |
| Dynamic value references, task values | Jinja templates, XCom |

## The same job as a DAG

A sketch, written for this project and **not run**: the operator arguments differ between versions
of the Databricks provider, so treat it as the shape and check the provider's documentation.

```python
import pendulum
from datetime import timedelta

from airflow import DAG
from airflow.providers.databricks.operators.databricks import DatabricksSubmitRunOperator

LANDING = "/Volumes/workspace/bronze/landing"
WHEEL = "/Volumes/workspace/bronze/landing/artifacts/payments_lakehouse-0.1.0-py3-none-any.whl"  # placeholder
PIPELINE_ID = "<id of payments_lakehouse_pipeline>"
CHECKS_DIR = "/Workspace/<deployed path>/sql/checks"
ENV = {"environment_key": "default", "spec": {"environment_version": "4", "dependencies": [WHEEL]}}


def wheel(task_key: str, entry_point: str, parameters: list[str]) -> DatabricksSubmitRunOperator:
    task = {
        "task_key": task_key,
        "environment_key": "default",
        "python_wheel_task": {
            "package_name": "payments_lakehouse",
            "entry_point": entry_point,
            "parameters": parameters,
        },
    }
    return DatabricksSubmitRunOperator(
        task_id=task_key, json={"run_name": task_key, "tasks": [task], "environments": [ENV]}
    )


with DAG(
    dag_id="payments_lakehouse_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 9, 20, tz="Europe/London"),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=1),
    default_args={"email": ["data-alerts@example.com"], "email_on_failure": True},
) as dag:
    generate = wheel("generate_daily_files", "generate-day", ["--landing", LANDING, "--date", "{{ ds }}"])
    holidays = wheel("pull_holidays", "pull-holidays", ["--dest", f"{LANDING}/bank_holidays"])
    refresh = DatabricksSubmitRunOperator(
        task_id="refresh_pipeline",
        json={"run_name": "refresh_pipeline", "tasks": [
            {"task_key": "refresh_pipeline", "pipeline_task": {"pipeline_id": PIPELINE_ID}}]},
        retries=1,
        retry_delay=timedelta(minutes=2),
    )
    verify = wheel("verify", "run-checks", ["--checks-dir", CHECKS_DIR])

    [generate, holidays] >> refresh >> verify
```

## The differences worth being able to explain

1. **The logical date.** Airflow gives every DAG run a logical date, so a retry, a clear or a backfill
   always works on the same day. A Databricks Job has no such idea. Here the run's start date is
   passed to the task (`--today`) and "yesterday" is worked out inside it, so the day does not depend
   on when the task happens to run. Because every step is idempotent (ADR-023), repeating a day by
   hand is also safe, whichever tool does it.
2. **Who runs the scheduler.** A Databricks Job is scheduled by the platform. Airflow needs a
   scheduler, a metadata database and workers, or a managed service, plus upgrades and monitoring.
3. **Where the code runs.** A Databricks task runs on Databricks compute. In Airflow the task runs on
   an Airflow worker, and to use Databricks it submits a run and polls until it finishes. That adds a
   moving part, and it is what lets one DAG also call other systems.
4. **Backfills.** Airflow can run a range of past logical dates in bulk. Here you run the job once per
   day with `run_date`, which is safe for the same reason as above.
5. **Alerts.** Both alert on a task that has failed for good, after its retries. Airflow can also
   alert on each retry and on a missed SLA, if you ask. Here the alert is the email in ADR-022.
6. **Deployment.** A bundle deploys the job, the pipeline and the code together and deletes what is no
   longer in the repository. An Airflow DAG file is only the schedule: the code it calls is deployed
   separately.
