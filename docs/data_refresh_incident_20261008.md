# October 8 daily collection failure

The run required October 7 rows and failed on seven symbols after 3,758 successes.

- `196490` is officially delisted on October 7, after final liquidation trading
  on October 6. DART receipt `20260921900630`, document `11587591`, explicitly
  gives these dates. The sourced lifecycle registry now deactivates this listing
  on its effective date. Historical silver is retained.
- Direct read-only KIS queries for `0120X0`, `0191M0`, `0191W0`, `0203R0`,
  `292770`, and `407300` returned October 7 rows with positive and consistent
  OHLC and zero volume/value. These products must not be arbitrarily excluded
  or their missing data assumed to be delisting. The captured failure log alone
  does not establish what response they returned during the failed run.

Official delisting source:
https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260921900630

Separate in-progress workspace changes to OHLCV normalization and diagnostics
were detected during investigation and preserved. Their authorship and inclusion
in deployment must be resolved independently of the sourced lifecycle update.
