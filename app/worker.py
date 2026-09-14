"""Railway cron entrypoint. Set JOB=live, contracts, or all."""
from __future__ import annotations
import os
from .collectors import austender_contracts, austender_live
from .db import SessionLocal, init_db
from .services.prospecting import calculate_matches, discover_prospects

def main():
    init_db(); job=os.getenv("JOB","all")
    with SessionLocal() as db:
        if job in {"live","all"}: austender_live.collect(db)
        if job in {"contracts","all"}: austender_contracts.collect(db)
        discover_prospects(db); calculate_matches(db)
if __name__=="__main__": main()

