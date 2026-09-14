# Data sources and attribution

## AusTender Current Approaches to Market

- Official source: <https://www.tenders.gov.au/atm>
- Structured feed: <https://www.tenders.gov.au/public_data/rss/rss.xml>
- Detail pages: `https://www.tenders.gov.au/Atm/Show/{official-id}`
- Purpose: genuine current Australian Government Approaches to Market.
- Method: read the public RSS feed, then make a rate-limited request to each returned official detail page for identity, agency, category, type and close time.
- Update: NixSec is configured to poll no more often than hourly.
- Attribution: source links and provenance are retained. NixSec does not claim ownership of government records.
- Fields: ATM ID, title, summary, agency, publication time, closing time, category/UNSPSC, ATM type, source URL, first/last seen, status.
- Validation: on 14 September 2026 the Work runtime retrieved HTTP 200, 77 RSS entries and multiple HTTP 200 detail pages. See `validation/austender_live_validation.json`.
- Limitation: RSS membership alone is not treated as proof an item remains live. Status is derived from the detail-page ACT-local close timestamp. The feed briefly retained one just-expired record during validation.

## AusTender OCDS Contract Notice API

- Official documentation: <https://github.com/austender/austender-ocds-api>
- API: `https://api.tenders.gov.au/ocds/`
- Purpose: awarded Contract Notice history from 1 January 2013—not live tenders.
- Method: official OCDS 1.1 REST date-range queries with incremental upsert and version preservation.
- Licence: responses declare CC Attribution 3.0 Australia: <https://creativecommons.org/licenses/by/3.0/au/>.
- Attribution: Department of Finance / AusTender.
- Validation: a September 2026 day query returned a valid 182,801-byte OCDS package. This was not counted as live validation.

## Australian Business Register / ABN Lookup

- Official source: <https://abr.business.gov.au/Tools/WebServices>
- Requirements: registered GUID and compliance with ABN Lookup web-service terms.
- Status: disabled unless `ABR_GUID` is configured. No unnecessary personal information is collected.

## State sources

Buying for Victoria was confirmed to publish current tenders publicly, but it is not a core collector in this release because direct AusTender collection passed validation. Other states require individual legal and technical validation before being enabled.

