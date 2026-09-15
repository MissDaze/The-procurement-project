"""Railway worker entrypoint.

JOB=live       refresh current ATMs
JOB=contracts  ingest the latest published Contract Notices
JOB=backfill   backfill historical Contract Notices using BACKFILL_DAYS (default 365)
JOB=all        run live + latest contracts
"""
from __future__ import annotations

import os

from .collectors import austender_contracts, austender_live
from .db import SessionLocal, init_db
from .services.contact_enrichment import enrich_missing_prospects
from .services.prospecting import calculate_matches, discover_prospects


def main():
    init_db()
    job = os.getenv("JOB", "all").lower()
    with SessionLocal() as db:
        if job in {"live", "all"}:
            austender_live.collect(db)
        if job in {"contracts", "all"}:
            austender_contracts.collect(db)
        if job == "backfill":
            days = int(os.getenv("BACKFILL_DAYS", "365"))
            chunk_days = int(os.getenv("BACKFILL_CHUNK_DAYS", "7"))
            austender_contracts.backfill(db, days=days, chunk_days=chunk_days)
        discover_prospects(db)
        calculate_matches(db)
        if job in {"live", "all"}:
            enrich_missing_prospects(db, limit=20)


if __name__ == "__main__":
    main()
