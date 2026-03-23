# KIS Data Platform v5 (실전 운영용)

이 패키지는 아래 실전 운영 흐름을 포함합니다.

1. KIS mst 다운로드 / 파싱 / universe 생성
2. 10년치 백필(backfill) 수집
3. 일일 증분 수집(incremental)
4. raw JSON + silver parquet 저장
5. collect_state / run_log / delta_history 메타 관리
6. delta 생성
7. staging 업로드
8. GitHub Actions로 Release 업로드 + data_manifest 갱신

## 주요 개선점(v4 대비)
- `backfill_job.py` 추가
- `incremental_job.py` 추가
- 공통 설정/로깅 모듈 분리
- run_log 구조 보강
- storage 계층 분리
- 운영용 스크립트 제공

## 빠른 시작

```bash
cp .env.example .env

python -m src.master.download_master
python -m src.master.parse_kospi_mst
python -m src.master.parse_kosdaq_mst
python -m src.master.build_universe

# 소규모 테스트 수집
python -m src.jobs.incremental_job --start-date 2025-12-20 --end-date 2025-12-31 --limit 10

# 10년치 백필 예시 (KIS 정책에 맞춰 순차 수집)
python -m src.jobs.backfill_job --start-date 2016-01-01 --end-date 2025-12-31 --limit 50

# delta 생성 / 업로드 / 액션 트리거
python -m src.transform.make_delta --start-date 2025-12-20 --end-date 2025-12-31 --tag data-delta-20251231-1800
python -m src.publish.upload_to_staging --tag data-delta-20251231-1800
python -m src.publish.trigger_github_actions --tag data-delta-20251231-1800 --asset-url "https://your-staging.example.com/data-delta-20251231-1800.parquet"

# snapshot 생성 / 액션 트리거
python -m src.jobs.full_snapshot_job --mode snapshot
python -m src.publish.trigger_github_actions --tag data-snapshot-20251231-1800 --asset-url "https://your-public-download.example.com/data-snapshot-20251231-1800.parquet"
```

## 초기 10년치 수집 + 백업 + 이후 운영

1. **초기 10년치 전체 백필 (예시)**  
   종목 수가 많고 기간이 길기 때문에, 구간을 나누어 순차 실행하는 스크립트를 제공합니다.

   ```bash
   # 1회차, 2회차, 3회차를 자동으로 판단해서 실행
   # (이미 성공한 구간은 run_log 기준으로 스킵, 요청은 1년 단위로 쪼개서 수행)
   python -m scripts.run_full_backfill
   ```

2. **한 번에 긴 기간 요청 제한**  
   KIS API는 1회 요청당 반환 건수/기간 제한이 있어, **요청 기간을 1년(365일) 단위로 쪼개서** 여러 번 호출한 뒤 결과를 합쳐 저장합니다.  
   `.env`의 `BACKFILL_CHUNK_DAYS=365` 로 조정 가능합니다.

3. **갭 채우기 (비어 있는 구간만 재수집)**  
   수집 후 silver를 연도별로 검사해, **연도당 row 수가 부족한 (symbol, 연도) 구간만** 다시 수집합니다.

   ```bash
   # 2016~2025 구간 중 비어 있는 연도만 재수집
   python -m src.jobs.gap_fill_job --target-start 2016-01-01 --target-end 2025-12-31
   ```

4. **스냅샷 백업 (data + meta + data_manifest)**  
   초기 수집이 끝난 시점의 데이터를 zip으로 스냅샷 저장합니다.

   ```bash
   python -m scripts.backup_snapshot
   # backups/snapshot_YYYYMMDD_HHMMSS.zip 생성
   ```

5. **이후 일일 증분 수집**  
   이후에는 신규 거래일 단위로 incremental 수집을 실행합니다.  
   `collect_state`의 마지막 성공일을 기준으로 자동으로 범위를 계산합니다.

   ```bash
   # 예: 매일 1회 스케줄러에 등록 (전날까지 자동 수집)
   python -m scripts.run_daily_collect
   ```

   **최신일 volume=0 보정** (장중 스냅샷 등으로 당일 봉 거래량이 0으로 남은 종목만 해당 일 재수집):

   ```bash
   python -m scripts.repair_zero_volume_day
   python -m scripts.repair_zero_volume_day --date 2026-03-19 --dry-run
   ```

   **최근 N일 일괄 점검·보정** (GitHub Actions: `Audit and repair last week (silver)` — 수동 실행 또는 매주 일요일 UTC 21:00):

   ```bash
   python -m scripts.audit_repair_last_week --days 7
   python -m scripts.audit_repair_last_week --days 7 --audit-only
   python -m scripts.audit_repair_last_week --days 7 --skip-incremental --skip-zero-repair
   python -m scripts.audit_repair_last_week --days 7 --low-volume-ratio 0.1 --low-volume-lookback-days 30 --min-baseline-volume 1000 --min-history-points 10
   ```

   **증분 구간 수동 복구** (스케줄 미실행 등으로 특정 구간이 비었을 때):

   ```bash
   # 3/10 ~ 3/18 구간만 수집 (날짜는 실제 비어 있는 구간으로 변경)
   python -m src.jobs.incremental_job --start-date 2026-03-10 --end-date 2026-03-18
   python -m src.jobs.gap_fill_job --target-start 2026-03-10 --target-end 2026-03-18 --merge
   ```

6. **실패 종목만 재수집**  
   `collect_state`에 `last_error`가 있는 종목만 골라서 지정 기간으로 다시 수집합니다.

   ```bash
   python -m src.jobs.retry_failed_job --start-date 2025-01-01 --end-date 2026-03-09
   ```
