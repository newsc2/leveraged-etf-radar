"""S3 upload logic — same pattern as macro-dashboard-v3."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from src.config import S3_BUCKET, S3_PREFIX

logger: logging.Logger = logging.getLogger(__name__)


def upload_to_s3(output_path: Path) -> None:
    """Upload the generated HTML file to s3://newsc2.com/projects/leveraged-etf-radar/app/."""
    target = f"s3://{S3_BUCKET}/{S3_PREFIX}/index.html"
    logger.info(f"Uploading to {target} ...")
    try:
        subprocess.run(
            [
                "aws", "s3", "cp", str(output_path), target,
                "--content-type", "text/html",
                "--cache-control", "public, max-age=300",
            ],
            check=True, capture_output=True, text=True,
        )
        logger.info("Upload complete!")
    except subprocess.CalledProcessError as e:
        logger.error(f"S3 upload failed: {e.stderr}")
        sys.exit(1)
