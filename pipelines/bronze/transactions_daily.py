"""Bronze: the daily transaction files written by the generator (JSON Lines).

Known columns are typed with schema hints. New columns are accepted when they appear
(schemaEvolutionMode addNewColumns), which is how the `channel` field added on 2026-09-28 is
handled. Anything that conflicts with a hinted type lands in _rescued_data. event_ts stays a
string here because the feed contains unparseable timestamps; silver parses it and quarantines
the bad rows.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING = "/Volumes/workspace/bronze/landing/transactions_daily/"

HINTS = (
    "event_id STRING, event_ts STRING, type STRING, amount DECIMAL(18,2), "
    "customer_id STRING, counterparty_id STRING, "
    "origin_balance_before DECIMAL(18,2), origin_balance_after DECIMAL(18,2), "
    "status STRING, decline_reason STRING, is_fraud INT, source_system STRING"
)


@dp.table(
    name="workspace.bronze.transactions_daily",
    comment="Raw daily transactions from the generator, defects included, one row per line",
)
@dp.expect("no_contract_violations", "_rescued_data IS NULL")
def transactions_daily():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("pathGlobFilter", "*.jsonl")  # only real data files, never temp or stray files
        .option("cloudFiles.schemaHints", HINTS)
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(LANDING)
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.col("_metadata.file_modification_time").alias("_source_modified_at"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )
