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
