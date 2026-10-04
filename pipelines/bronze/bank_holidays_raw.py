"""Bronze: the GOV.UK bank holidays API payload, stored exactly as received.

Each landed file is one JSON document, read whole into a single string column. Parsing happens in
silver, so a change in the API's shape can never break ingestion (ADR-009).
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING = "/Volumes/workspace/bronze/landing/bank_holidays/"


@dp.table(
    name="workspace.bronze.bank_holidays_raw",
    comment="GOV.UK bank holidays payload as received, one row per landed file",
)
@dp.expect("payload_present", "payload IS NOT NULL AND length(payload) > 0")
def bank_holidays_raw():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("wholeText", "true")
        .option("pathGlobFilter", "*.json")  # skips the .tmp file used while a pull is landing
        .load(LANDING)
        .select(
            F.col("value").alias("payload"),
            F.col("_metadata.file_path").alias("_source_file"),
            F.col("_metadata.file_modification_time").alias("_source_modified_at"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )
