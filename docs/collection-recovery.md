# Daily collection recovery

## Why daily runs repeatedly stopped

The KIS master can retain a delisted listing, or lag a trading-status change.
The daily collector previously retried the price request twice and treated every
missing target-date row as an unresolved failure. The strict publish gate then
correctly blocked the entire release. Adding one delisting override resolved one
listing but could not handle the next lifecycle event. The daily universe also
did not filter listings beginning after the requested target date.

## Recovery behavior

- Exclude future listings when selecting the daily universe.
- Retry failed symbols up to three times, with 2 and 4 seconds between passes.
- After retries, query KIS `search-stock-info` only for absent target-date rows.
  Invalid target-date price rows and request failures are still failures.
- Require the returned `std_pdno` to match the universe ISIN.
- Accept a valid market-specific or general delisting date on or before the target.
  Ignore an old delisting date if the market listing date is newer.
- Accept `tr_stop_yn=Y` only for today's Korean market date. Current trading
  status cannot establish that a historical date was halted.
- Persist confirmed exclusions transactionally to the universe used by gap fill
  and snapshot validation. Keep historical silver and the last successful price
  date. The next master refresh rebuilds current status, so a halt exclusion is
  not permanent.
- If evidence is missing, mismatched, malformed, or unavailable, retain failure
  and block publication. An empty response alone never deactivates a listing.

`collect_rows` returns handled and failed counts; handled includes independently
verified exclusions. The JSON report distinguishes actual price successes from
exclusions. Reports are written under `data/reports/collection/` and uploaded as
`collection-diagnostics-<run_id>-<attempt>` even when the collection step fails.
The report contains the failed symbol, original error, status evidence, recovery
outcome, and any lookup error. Raw price payloads retain the existing storage path.

## References

- [KIS stock-info request](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/search_stock_info/search_stock_info.py)
- [KIS stock-info response fields](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/search_stock_info/chk_search_stock_info.py)

If KIS price and stock-info services both lack up-to-date lifecycle information,
automatic exclusion is intentionally unavailable. Inspect the retained report and
use a sourced lifecycle override after verifying the exchange notice.
