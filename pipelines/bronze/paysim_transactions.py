"""Bronze: the PaySim historical transactions, loaded from the landing volume.

Raw and append-only. The explicit schema is the data contract (docs/data_profile.md): a value that
does not fit its type lands in _rescued_data instead of being lost or crashing the load.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING = "/Volumes/workspace/bronze/landing/paysim/"

# Money is DECIMAL(18,2), not DOUBLE. Checked against the real file: no value has digits beyond
# two decimal places, so the conversion is lossless. Values of 10 million or more are written in
# scientific notation in the file (for example 1.010284203E7), which the CSV reader must accept.
SCHEMA = (
    "step INT, type STRING, amount DECIMAL(18,2), nameOrig STRING, "
    "oldbalanceOrg DECIMAL(18,2), newbalanceOrig DECIMAL(18,2), nameDest STRING, "
    "oldbalanceDest DECIMAL(18,2), newbalanceDest DECIMAL(18,2), "
    "isFraud INT, isFlaggedFraud INT, _rescued_data STRING"
)


@dp.table(
    name="workspace.bronze.paysim_transactions",
    comment="Raw PaySim transactions, one row per source row, typed by the data contract",
)
@dp.expect("no_contract_violations", "_rescued_data IS NULL")
def paysim_transactions():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("header", "true")
        .schema(SCHEMA)
        .load(LANDING)
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.col("_metadata.file_modification_time").alias("_source_modified_at"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )
