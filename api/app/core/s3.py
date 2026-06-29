"""Minimal S3 helpers for the BE — download index TIFs pre-fetched at startup.
Reads credentials from env directly; no pipeline.config dependency."""
import os

import boto3
from botocore.client import Config
from loguru import logger


def _client():
    cfg = Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 1})
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        region_name="auto",
        config=cfg,
    )


def download_from_s3(s3_key: str, local_path: str) -> bool:
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        logger.info(f"Downloading s3://{bucket}/{s3_key} → {local_path}")
        _client().download_file(bucket, s3_key, local_path)
        return True
    except Exception as e:
        logger.error(f"S3 download failed for {s3_key}: {e}")
        return False


def check_s3_file_exists(s3_key: str) -> bool:
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        _client().head_object(Bucket=bucket, Key=s3_key)
        return True
    except Exception:
        return False
