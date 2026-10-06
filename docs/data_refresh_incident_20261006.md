# 2026-10-06 report data refresh incident

The report generated at 2026-10-06 07:59 KST used
`data-snapshot-20260929-0750`. Its analysis date reflects the source data;
changing the HTML date would misrepresent the prices.

## Evidence

- GitHub run `36725799537` (September 30): daily collection ended with
  `success=4272 failed=62`. Missing target rows included ordinary stocks and
  unsupported longer product codes such as `Q610082`.
- GitHub run `37364597735` (October 6 early morning KST): the target was
  October 5, an exchange holiday. All 3,506 symbols failed the required-date
  check. Snapshot creation and publication were skipped.
- The latest published price snapshot remained September 29; newer
  `theme-sector-*` releases are a different dataset.

## Changes

- Production collection resolves its target using the KIS opening calendar
  (`chk-holiday`, `CTCA0903R`, `opnd_yn`). Missing calendar information blocks
  collection; empty price responses are not treated as proof of a holiday.
- Six-character stock codes containing letters are supported; longer product
  codes remain excluded.
- KOSPI/KOSDAQ master parsing preserves trading-halt and listing-date fields.
  Tail widths exclude line endings, preserving the final character of names.
- Collection and gap detection exclude stocks flagged as halted. Real API
  failures continue to block publication instead of silently accepting stale data.

Official specifications:
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/chk_holiday/chk_holiday.py
- https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kospi_code_mst.py
- https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kosdaq_code_mst.py

## Verification and recovery

83 tests passed, one skipped. Public master downloads were parsed successfully;
40 KOSPI and 86 KOSDAQ entries were flagged as halted in the downloaded files.
Live authenticated KIS calendar/price requests have not been run locally.

After deploying these changes to the workflow's branch, rerun
`refresh-snapshot-from-release.yml`. The workflow already rebuilds the universe,
collects missing dates, and validates the snapshot before publication. Then
regenerate the Malbec report and verify its source release and analysis date.
The local changes alone do not update the published snapshot or downloaded HTML.

## Follow-up: audit/repair workflow

The reported 09:24 KST failure came from `audit_repair_last_week`, which still
called the weekday-only resolver without a calendar loader. That repair entry
point now uses the KIS opening calendar too. Read-only audit and dry-run retain
their no-KIS-call behavior; explicit end dates remain explicit overrides.

Empty-day `SUM` results are coalesced to zero so audit output no longer attempts
to convert NaN to an integer. After successful incremental collection, the
repair stage reloads silver paths and date counts to include newly filled dates.
Regression verification: 86 tests passed, one skipped.

## Follow-up: delisted listing 084180

The next run collected 3,761 symbols and failed only on Suseong Webtoon
(`084180`). Official DART disclosure `20260916900675`, document `11582487`,
states final liquidation trading through September 30 and delisting October 1.
The vendor master was still treated as active. A sourced lifecycle registry now
sets this listing inactive from its delisting date, matching market, short code,
and ISIN. Collection, gap detection, repair and snapshot validation share the
active-universe predicate, so no arbitrary missing-price error is ignored.
Historical silver is preserved. Empty initial audit counts precede incremental
collection and do not mean that the 3,761 successful writes were absent.

## Follow-up: theme investor coverage

Run `37411243835` started October 6 at 12:55 KST and failed with investor
coverage 0/233. The official `inquire-investor` documentation says same-day
data is provided after market close. Retrying hundreds of symbols intraday
does not make that data available. Strict KRX investor collection now checks
the 15:30 KST close before starting expensive calls; the existing scheduled
workflow remains at 20:30 KST. After-close availability is still verified by
actual coverage and never assumed to be complete from the clock alone.

The official institution field `orgn_ntby_tr_pbmn` is now recognized, and strict
coverage requires both foreign and institution values (zero remains valid).
Run the workflow after close for complete same-day flow. An intraday report can
be generated without `--strict-completeness`; unavailable flow displays `-`.

Official API references:
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/inquire_investor.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/chk_inquire_investor.py

Verification: 92 tests passed, one skipped. Price snapshot recovery separately
succeeded: remote manifest advances through `data-snapshot-20261006-1218`.
