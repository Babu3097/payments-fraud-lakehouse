"""Run a whole pipeline step on fixture tables, so a test can start from bronze and read silver."""

from tests.helpers.lakeflow_sql import find, load, localize
from tests.helpers.spark_support import make_table
from tests.sql.fixtures import BANK_HOLIDAYS, PAYSIM, TRANSACTIONS_DAILY


def build_unified(spark, generated=(), paysim_rows=(), holidays=()):
    """Run both silver unify flows and keep the union as `transactions_unified`, the way the
    private working table is filled in the pipeline."""
    make_table(spark, "workspace.bronze.transactions_daily", TRANSACTIONS_DAILY, list(generated))
    make_table(spark, "workspace.bronze.paysim_transactions", PAYSIM, list(paysim_rows))
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, list(holidays))
    statements = load("silver/transactions_unified.sql")
    generated_df, paysim_df = (
        spark.sql(localize(find(statements, name).query))
        for name in ("unify_generated", "unify_paysim")
    )
    unified = generated_df.unionByName(paysim_df, allowMissingColumns=True)
    unified.createOrReplaceTempView("transactions_unified")
    return unified


# ---- a whole small warehouse -------------------------------------------------------------------

from tests.helpers.spark_support import make_table as _make_table  # noqa: E402
from tests.sql.fixtures import PROFILE, paysim, profile, txn  # noqa: E402

PAYSIM_ROWS = [
    paysim(step=1, amount="100.00"),
    paysim(step=2, type="TRANSFER", amount="500.00", nameOrig="C2", nameDest="C3", isFraud=1),
    paysim(step=30, type="CASH_OUT", amount="250.00", nameOrig="C4", nameDest="C5"),
]
GENERATED_ROWS = [
    txn(event_id="G1", customer_id="CG1", counterparty_id="MG1", amount="25.50"),
    txn(event_id="G2", customer_id="CG1", counterparty_id="CG2", type="TRANSFER", amount="100.00",
        is_fraud=1, event_ts="2026-10-06T13:00:00Z"),
    txn(event_id="G3", customer_id="CG2", counterparty_id="MG1", amount="-5.00"),  # quarantined
    txn(event_id="G1", customer_id="CG1", counterparty_id="MG1", amount="25.50",
        _ingested_at="2026-10-07 07:00:00"),  # a duplicate of G1 that arrived later
    txn(event_id="G5", customer_id="CG2", counterparty_id="MG1", type="REFUND", amount="10.00"),
]  # fmt: skip
PROFILE_ROWS = [
    profile("CG1", "2026-09-20T00:00:00Z", "standard"),
    profile("CG1", "2026-10-06T12:00:00Z", "premium"),
    profile("CG2", "2026-09-20T00:00:00Z", "standard"),
    profile("MG1", "2026-09-20T00:00:00Z", "retail", kind="merchant"),
]


def _scd2_history(spark):
    """Stand-in for AUTO CDC ... STORED AS SCD TYPE 2 TRACK HISTORY ON segment, region, which only
    runs on Databricks. A new version starts when segment or region changes, runs until the next
    one starts, and the newest has no end."""
    spark.sql(
        """
        WITH events AS (
            SELECT *,
                lag(segment) OVER w AS previous_segment,
                lag(region) OVER w AS previous_region
            FROM silver_customer_profile_events
            WINDOW w AS (PARTITION BY customer_id ORDER BY changed_at)
        ),
        starts AS (
            SELECT * FROM events
            WHERE previous_segment IS NULL
                OR previous_segment <> segment OR previous_region <> region
        )
        SELECT customer_id, entity_type, segment, region, changed_at,
            changed_at AS __start_at,
            lead(changed_at) OVER (PARTITION BY customer_id ORDER BY changed_at) AS __end_at
        FROM starts
        """
    ).createOrReplaceTempView("customer_history")


def build_universe(spark, tamper=None):
    """Run the real silver and gold SQL on a small consistent world, and register every table.

    The two Auto CDC steps are stood in for (see _scd2_history and the deduplication below).
    `tamper` maps a table name to a function that changes that table as it is registered, so a test
    can corrupt one table and let everything built from it follow.
    """
    tamper = tamper or {}

    def register(name, frame):
        # Each table is stored as it is registered (a local checkpoint), so the next step reads rows
        # instead of re-running everything before it. Without this the reconciliation's 25
        # subqueries each replay the whole chain, and a dozen rows take many minutes.
        frame = tamper.get(name, lambda f: f)(frame)
        frame.localCheckpoint().createOrReplaceTempView(name)

    register("transactions_unified", build_unified(spark, GENERATED_ROWS, PAYSIM_ROWS, []))
    _make_table(spark, "workspace.bronze.customer_profile_changes", PROFILE, PROFILE_ROWS)

    # silver
    valid_statement = find(load("silver/transactions.sql"), "transactions_valid")
    register("transactions_valid", spark.sql(localize(valid_statement.query)))
    # Stand-in for AUTO CDC ... STORED AS SCD TYPE 1: keep the newest copy of each event_id.
    valid = spark.sql(
        "SELECT *, row_number() OVER (PARTITION BY event_id ORDER BY _ingested_at DESC) AS _rank"
        " FROM transactions_valid"
    )
    register(
        "silver_transactions",
        valid.where("_rank = 1").drop("_rank", "failed_checks", "event_ts_raw"),
    )
    quarantine = find(load("silver/transactions_quarantine.sql"), "transactions_quarantine")
    register("silver_transactions_quarantine", spark.sql(localize(quarantine.query)))
    events = find(load("silver/customer_profile_events.sql"), "customer_profile_events")
    register("silver_customer_profile_events", spark.sql(localize(events.query)))

    # gold
    _scd2_history(spark)
    for source, name in (
        ("dim_type", "gold_dim_type"),
        ("dim_date", "gold_dim_date"),
        ("dim_customer", "gold_dim_customer"),
    ):
        statement = find(load(f"gold/{source}.sql"), source)
        register(name, spark.sql(localize(statement.query)))
    fact = find(load("gold/fact_transactions.sql"), "fact_transactions")
    register("gold_fact_transactions", spark.sql(localize(fact.query)))
    kpi = find(load("gold/kpi_daily.sql"), "kpi_daily")
    register("gold_kpi_daily", spark.sql(localize(kpi.query)))
    recon = find(load("gold/reconciliation.sql"), "reconciliation")
    return spark.sql(localize(recon.query))
