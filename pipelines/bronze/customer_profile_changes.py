"""Bronze: customer and merchant change events (CSV), the source for the SCD2 dimension.

An initial load of every entity, then a few hundred changes a day. This feed is clean, so
changed_at is typed as a timestamp here; silver orders the events by it to build history.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING = "/Volumes/workspace/bronze/landing/customer_profile/"

SCHEMA = (
    "customer_id STRING, entity_type STRING, segment STRING, region STRING, "
    "changed_at TIMESTAMP, _rescued_data STRING"
)


@dp.table(
    name="workspace.bronze.customer_profile_changes",
    comment="Raw customer and merchant change events from the generator",
)
@dp.expect("no_contract_violations", "_rescued_data IS NULL")
def customer_profile_changes():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("header", "true")
        .option("pathGlobFilter", "*.csv")  # only real data files, never temp or stray files
        .option("timestampFormat", "yyyy-MM-dd'T'HH:mm:ss'Z'")
        .option("timeZone", "UTC")
        .schema(SCHEMA)
        .load(LANDING)
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.col("_metadata.file_modification_time").alias("_source_modified_at"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )
