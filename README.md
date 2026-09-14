# NixSec Procurement Intelligence

A browser-based procurement-intelligence, prospecting and lightweight CRM application. It creates value with zero clients by collecting official Australian procurement data, discovering suppliers, calculating deterministic prospect scores and producing branded sales previews. Converted prospects retain their history and become monitored clients.

## Credibility rules

- **LIVE TENDER / CLOSING SOON:** an official Approach to Market whose parsed closing timestamp is still in the future.
- **AWARDED CONTRACT:** an official historical/current Contract Notice.
- **CONTRACT EXPIRING:** an awarded contract approaching its recorded end date.
- **LIKELY RECOMPETE — INFERRED:** a deterministic prediction, never an advertised tender.

AI never creates procurement facts or numeric scores.

## Architecture

FastAPI, Jinja, responsive CSS, SQLAlchemy, SQLite development, PostgreSQL production, HTTPX collectors, optional OpenRouter commentary and WeasyPrint PDF output. The canonical NixSec cover is always page 1.

## Railway deployment

1. Create a Railway project from this repository and add PostgreSQL.
2. Set `SESSION_SECRET`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` as variables.
3. Deploy; the Dockerfile and `/health` check are automatic.
4. Log in and select **Initialise NixSec Intelligence**.
5. Add cron services using the same repository and `python -m app.worker`: `JOB=live` hourly and `JOB=contracts` daily.

Normal use requires no CLI.

## Environment

`DATABASE_URL`, `SESSION_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, optional `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `AI_ENABLED`, `NIXSEC_WEBSITE`, and optional authorised `ABR_GUID`. Never commit secrets.

## First run

An empty dashboard offers initialisation. It creates tables, collects live ATMs and Contract Notices, normalises suppliers/agencies, aggregates metrics, discovers and scores prospects, and calculates explainable opportunity matches. Reports use verified facts; missing ABNs/industries render `Not Available`. OpenRouter output is labelled `AI INTERPRETATION` and the product works without it.

## Local verification

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload
```

Repeat the live-source gate with `python ../validation/validate_austender_live.py`.

## Troubleshooting

- No live tenders: inspect Data Collection; exact HTTP/parser errors are recorded and no opportunities are fabricated.
- Empty prospects: awarded Contract Notices are required for supplier discovery.
- PDF failure: use the supplied Dockerfile with Pango libraries.
- AI unavailable: deterministic collection, scoring and reports continue.
- Duplicates: source IDs are unique and collectors use upsert/version logic.

See [DATA_SOURCES.md](DATA_SOURCES.md) for licensing, attribution and limitations.
