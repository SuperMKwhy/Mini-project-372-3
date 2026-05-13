import azure.functions as func
import logging
import json
import os
import pymssql
from datetime import datetime
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

IDEAL_FLOW_LPM = 220.0

# Maps PLC tag suffix -> (kpi_column, bay_number_in_failure_table, failure_table)
FT_TAG_MAP = {
    "FT_100_1": ("diesel_1",    1, "S3A_FAILURE_LOGS"),
    "FT_100_2": ("diesel_2",    2, "S3A_FAILURE_LOGS"),
    "FT_100_3": ("diesel_3",    3, "S3A_FAILURE_LOGS"),
    "FT_100_4": ("diesel_4",    4, "S3A_FAILURE_LOGS"),
    "FT_200_1": ("gasohol95_5", 1, "S3B_FAILURE_LOGS"),
    "FT_200_2": ("gasohol95_6", 2, "S3B_FAILURE_LOGS"),
}

BAY_COLS = ["diesel_1", "diesel_2", "diesel_3", "diesel_4", "gasohol95_5", "gasohol95_6"]


def _get_conn():
    return pymssql.connect(
        server=os.environ["SqlServer"],
        user=os.environ["SqlUser"],
        password=os.environ["SqlPassword"],
        database=os.environ["SqlDatabase"],
    )


def _ensure_kpi_table(cursor):
    cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'BAY_KPI')
        CREATE TABLE BAY_KPI (
            kpi_metric   NVARCHAR(100) NOT NULL PRIMARY KEY,
            diesel_1     FLOAT,
            diesel_2     FLOAT,
            diesel_3     FLOAT,
            diesel_4     FLOAT,
            gasohol95_5  FLOAT,
            gasohol95_6  FLOAT,
            updated_at   DATETIME2
        )
    """)


def _upsert_kpi(cursor, metric: str, values: dict):
    v = [values.get(c) for c in BAY_COLS]
    cursor.execute("""
        IF EXISTS (SELECT 1 FROM BAY_KPI WHERE kpi_metric = %s)
            UPDATE BAY_KPI
            SET diesel_1=%s, diesel_2=%s, diesel_3=%s, diesel_4=%s,
                gasohol95_5=%s, gasohol95_6=%s, updated_at=GETDATE()
            WHERE kpi_metric = %s
        ELSE
            INSERT INTO BAY_KPI
                (kpi_metric, diesel_1, diesel_2, diesel_3, diesel_4, gasohol95_5, gasohol95_6, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, GETDATE())
    """, (metric, *v, metric, metric, *v))


def _read_latest_blob() -> list:
    client = BlobServiceClient.from_connection_string(os.environ["BlobConnectionString"])
    container = client.get_container_client(os.environ["BlobContainerName"])
    prefix = os.environ.get("BlobPrefix", "") or None

    blobs = sorted(
        container.list_blobs(name_starts_with=prefix),
        key=lambda b: b.last_modified,
        reverse=True,
    )
    if not blobs:
        logging.warning("No blobs found in container")
        return []

    blob_data = container.get_blob_client(blobs[0].name).download_blob().readall()
    logging.info("Reading blob: %s", blobs[0].name)

    records = []
    for line in blob_data.decode("utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _compute_flow_pct(records: list) -> dict:
    """Average actual flow per bay as % of ideal (220 L/min)."""
    sums = {}
    counts = {}
    for r in records:
        tag = r["tag_name"].split(".")[-1]
        if tag in FT_TAG_MAP:
            col = FT_TAG_MAP[tag][0]
            sums[col] = sums.get(col, 0.0) + r["tag_value"]
            counts[col] = counts.get(col, 0) + 1

    result = {}
    for col in BAY_COLS:
        if counts.get(col, 0) > 0:
            avg = sums[col] / counts[col]
            result[col] = round(avg / IDEAL_FLOW_LPM * 100, 2)
        else:
            result[col] = 0.0
    return result


def _compute_failure_rate(cursor) -> dict:
    """Failure rate = total failures / total hours since first record (failures per hour)."""
    result = {}

    for tag_suffix, (col, bay_num, table) in FT_TAG_MAP.items():
        cursor.execute(f"""
            SELECT
                COUNT(*),
                DATEDIFF(HOUR, MIN(start_time), GETDATE())
            FROM {table}
            WHERE bay_number = %s
        """, (bay_num,))
        row = cursor.fetchone()
        count, total_hours = row if row else (0, 0)
        if total_hours and total_hours > 0:
            result[col] = round(count / total_hours, 6)
        else:
            result[col] = 0.0

    return result


# Change schedule to "0 * * * * *" (every minute) for production
@app.timer_trigger(schedule="*/5 * * * * *", arg_name="timer", run_on_startup=True, use_monitor=False)
def compute_kpi(timer: func.TimerRequest) -> None:
    logging.info("KPI ETL triggered at %s", datetime.utcnow().isoformat())

    records = _read_latest_blob()
    flow_pct = _compute_flow_pct(records)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_kpi_table(cur)
            _upsert_kpi(cur, "Performance_Flow_Pct", flow_pct)

            failure_rate = _compute_failure_rate(cur)
            _upsert_kpi(cur, "Failure_Rate_Min_Per_Hour", failure_rate)

        conn.commit()
        logging.info("KPI upserted — flow_pct: %s | failure_min: %s", flow_pct, failure_rate)
    finally:
        conn.close()
