# KIS Data Platform v5 운영 가이드

## v5 핵심 특징
- v4의 delta/release 구조 유지
- 백필(backfill) 전용 job 추가
- 병렬 수집용 `collect_backfill.py` 추가
- 공통 settings/logger/meta/parquet store 분리
- run_log/collect_state/delta_history 메타 관리 강화

## 운영 절차

### 1. 마스터 갱신
```bash
python -m src.master.download_master
python -m src.master.parse_kospi_mst
python -m src.master.parse_kosdaq_mst
python -m src.master.build_universe
```

### 2. 초기 10년 백필
```bash
python -m src.jobs.backfill_job --start-date 2016-01-01 --end-date 2025-12-31 --limit 50 --workers 4
```

### 3. 일일 증분
```bash
python -m src.jobs.incremental_job --start-date 2025-12-20 --end-date 2025-12-31 --limit 10
```

### 4. delta 생성 및 배포
```bash
python -m src.transform.make_delta --start-date 2025-12-20 --end-date 2025-12-31 --tag data-delta-20251231-1800
python -m src.publish.upload_to_staging --tag data-delta-20251231-1800
python -m src.publish.trigger_github_actions --tag data-delta-20251231-1800 --delta-url "https://your-staging.example.com/data-delta-20251231-1800.parquet"
```

## 실전 운영 팁
- `collect_backfill.py`는 ThreadPoolExecutor 기반입니다. API 제한 때문에 workers를 2~6부터 시험하세요.
- `make_delta.py`는 전체 parquet 스캔 방식이라 초기엔 단순하지만, 종목 수가 많아지면 changed_symbols 방식으로 개선하세요.
- staging은 현재 파일 복사형입니다. NAS/공유폴더에는 잘 맞고, S3/R2는 후속 확장 추천입니다.
- `kis_client.py`의 TR ID와 응답 키는 실제 계정/문서 기준으로 최종 검증이 필요합니다.
