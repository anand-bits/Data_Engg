"""
Lambda: JSON Reference Data → Silver Layer (Parquet)
────────────────────────────────────────────────────
Triggered by S3 event when new JSON lands in the Bronze bucket
under the reference_data prefix.

Improvements over original:
  - Data validation before writing
  - Deduplication of category records
  - Proper error handling with dead-letter alerting
  - Idempotent writes (overwrites partition, not append)
  - Structured logging

Environment Variables:
    S3_BUCKET_BRONZE            — Bronze bucket containing reference JSON
    S3_BUCKET_SILVER            — Target bucket for cleansed data (project-yt-silver-anand)
    GLUE_DB_SILVER              — Glue catalog database name
    GLUE_TABLE_REFERENCE        — Glue catalog table name
    SNS_ALERT_TOPIC_ARN         — SNS topic for alerts
"""

import json
import os
import logging
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
import awswrangler as wr
import pandas as pd

# ── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ──────────────────────────────────────────────────────────────────
BRONZE_BUCKET = os.environ.get("S3_BUCKET_BRONZE", "project-yt-bronze-anand")
SILVER_BUCKET = os.environ.get("S3_BUCKET_SILVER", "project-yt-silver-anand")
GLUE_DB = os.environ.get("GLUE_DB_SILVER", "yt_pipeline_silver_dev")
GLUE_TABLE = os.environ.get("GLUE_TABLE_REFERENCE", "clean_reference_data")
SNS_TOPIC = os.environ.get("SNS_ALERT_TOPIC_ARN", "arn:aws:sns:ap-southeast-2:908768512617:yt-data-pipeline-alerts-dev")
SILVER_PATH = f"s3://{SILVER_BUCKET}/youtube/reference_data/"

s3_client = boto3.client("s3")
sns_client = boto3.client("sns")


def read_json_from_s3(bucket: str, key: str) -> dict:
    """
    Read raw JSON from S3 using boto3 instead of awswrangler.
    awswrangler.s3.read_json() fails on the Kaggle/YouTube category JSON
    because it has mixed types (strings + nested arrays), which pandas
    can't parse directly into a DataFrame.
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read().decode("utf-8")
    return json.loads(content)


def validate_category_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and clean the category reference data.
    Returns cleaned DataFrame or raises ValueError.
    """
    if df.empty:
        raise ValueError("Empty DataFrame — no category items found")

    required_cols = {"id", "snippet.title"}
    actual_cols = set(df.columns)
    missing = required_cols - actual_cols
    if missing:
        # Try alternate column names from different API versions
        logger.warning(f"Missing expected columns: {missing}. Available: {actual_cols}")

    # Drop duplicate categories (same id)
    before = len(df)
    if "id" in df.columns:
        df = df.drop_duplicates(subset=["id"], keep="last")
    after = len(df)
    if before != after:
        logger.info(f"  Removed {before - after} duplicate categories")

    return df


def send_alert(subject: str, message: str):
    if SNS_TOPIC:
        try:
            sns_client.publish(TopicArn=SNS_TOPIC, Subject=subject[:100], Message=message)
        except Exception:
            logger.exception("Could not publish the reference-transform alert")


def _records_for_event(event: dict) -> list[dict]:
    """Return source bucket/key pairs for S3 events or Step Functions runs."""
    records = []

    for record in event.get("Records", []):
        s3_info = record["s3"]
        records.append((
            s3_info["bucket"]["name"],
            unquote_plus(s3_info["object"]["key"]),
        ))

    # EventBridge S3 notifications wrap the bucket and key under detail.
    if not records and isinstance(event.get("detail"), dict):
        detail = event["detail"]
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if bucket and key:
            records.append((bucket, unquote_plus(key)))

    if not records and event.get("triggered_by") == "step_functions":
        ingestion_id = event.get("ingestion_id")
        if not ingestion_id:
            raise ValueError("Step Functions payload is missing ingestion_id")

        try:
            ingestion_date = datetime.strptime(ingestion_id, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Invalid ingestion_id: {ingestion_id}") from exc

        regions = event.get("regions", [])
        if not regions:
            raise ValueError("Step Functions payload contains no successfully ingested regions")

        for region in regions:
            region = str(region).strip().lower()
            key = (
                "youtube/raw_statistics_reference_data/"
                f"region={region}/date={ingestion_date}/{region}_category_id.json"
            )
            records.append((BRONZE_BUCKET, key))

    return [{"bucket": bucket, "key": key} for bucket, key in records]


def lambda_handler(event, context):
    """Process category JSON from S3 events or the current Step Functions run."""
    records = _records_for_event(event)
    if not records:
        raise ValueError("No reference JSON objects were provided for processing")

    processed = []
    errors = []

    for record in records:
        key = record["key"]
        try:
            bucket = record["bucket"]

            logger.info(f"Processing: s3://{bucket}/{key}")

            # ── Read raw JSON ────────────────────────────────────────────
            # We use boto3 + json.loads instead of wr.s3.read_json() because
            # the category JSON has mixed types (strings like "kind"/"etag"
            # alongside a nested "items" array) which causes pandas to fail
            # with: "Mixing dicts with non-Series may lead to ambiguous ordering"
            raw_data = read_json_from_s3(bucket, key)

            # The YouTube/Kaggle JSON has { "kind": "...", "items": [...] }
            # We only care about the items array
            if "items" in raw_data and isinstance(raw_data["items"], list):
                df = pd.json_normalize(raw_data["items"])
            else:
                # Fallback: try to normalize the entire object
                df = pd.json_normalize(raw_data)

            logger.info(f"  Raw shape: {df.shape}")

            # ── Validate ─────────────────────────────────────────────────
            df = validate_category_data(df)

            # ── Add metadata columns ─────────────────────────────────────
            df["_ingestion_timestamp"] = datetime.now(timezone.utc).isoformat()
            df["_source_file"] = key

            # Extract region from the S3 key (e.g., region=US)
            region = "unknown"
            for part in key.split("/"):
                if part.startswith("region="):
                    region = part.split("=")[1]
                    break
            df["region"] = region

            logger.info(f"  Clean shape: {df.shape}, region: {region}")

            # ── Write to Silver layer as Parquet ─────────────────────────
            wr_response = wr.s3.to_parquet(
                df=df,
                path=SILVER_PATH,
                dataset=True,
                database=GLUE_DB,
                table=GLUE_TABLE,
                partition_cols=["region"],
                mode="overwrite_partitions",  # Idempotent per region
                schema_evolution=True,
            )

            logger.info(f"  Written to Silver: {SILVER_PATH}")
            processed.append({"key": key, "region": region, "rows": len(df)})

        except Exception as e:
            logger.error(f"Error processing record: {e}", exc_info=True)
            errors.append({"key": key if "key" in dir() else "unknown", "error": str(e)})

    # ── Summary ──────────────────────────────────────────────────────────
    if errors:
        send_alert(
            subject="[YT Pipeline] Silver reference transform failed",
            message=json.dumps(errors, indent=2),
        )
        raise RuntimeError(f"Reference transform failed for {len(errors)} object(s): {errors}")

    if not processed:
        raise RuntimeError("Reference transform completed without processing any objects")

    return {
        "statusCode": 200,
        "processed": processed,
        "errors": errors,
    }
