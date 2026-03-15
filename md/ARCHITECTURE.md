# KIS Data Platform v5 — 아키텍처 및 코드 구조

## 1. 프로젝트 폴더 구성

```mermaid
flowchart TB
    subgraph root["Sauvignon (프로젝트 루트)"]
        subgraph github[".github/workflows"]
            wf["publish-delta.yml"]
        end
        subgraph scripts["scripts"]
            run_master["run_master_refresh.py"]
        end
        subgraph md["md"]
            op["V5_OPERATION_GUIDE.md"]
            delta["DELTA_PUBLISH_GUIDE.md"]
            internal["INTERNAL_IMPLEMENTATION_GUIDE.md"]
        end
        subgraph src["src"]
            subgraph common["common"]
                settings["settings.py"]
                utils["utils.py"]
                logger["logger.py"]
            end
            subgraph clients["clients"]
                kis_client["kis_client.py"]
            end
            subgraph master["master"]
                download["download_master.py"]
                parse_kospi["parse_kospi_mst.py"]
                parse_kosdaq["parse_kosdaq_mst.py"]
                build_universe["build_universe.py"]
            end
            subgraph collect["collect"]
                base_collect["base_collect.py"]
                collect_daily["collect_daily.py"]
                collect_backfill["collect_backfill.py"]
            end
            subgraph jobs["jobs"]
                incremental_job["incremental_job.py"]
                backfill_job["backfill_job.py"]
            end
            subgraph storage["storage"]
                meta_store["meta_store.py"]
                parquet_store["parquet_store.py"]
            end
            subgraph transform["transform"]
                make_delta["make_delta.py"]
            end
            subgraph publish["publish"]
                upload_staging["upload_to_staging.py"]
                trigger_gh["trigger_github_actions.py"]
            end
        end
        manifest["data_manifest.json"]
        lock["LOCK.json"]
        env_ex[".env.example"]
        req["requirements.txt"]
        readme["README.md"]
    end
```

---

## 2. 레이어별 아키텍처

```mermaid
flowchart LR
    subgraph config["설정/공통"]
        Settings
        Logger
        Utils
    end

    subgraph ingest["수집 준비 (Master)"]
        Download
        ParseKospi
        ParseKosdaq
        BuildUniverse
    end

    subgraph collect_layer["수집 (Collect)"]
        IncrementalJob
        BackfillJob
        BaseCollect
    end

    subgraph external["외부"]
        KIS_API["KIS Open API"]
    end

    subgraph persist["저장 (Storage)"]
        MetaStore["meta_store"]
        ParquetStore["parquet_store"]
    end

    subgraph pipeline["변환/배포 (Transform & Publish)"]
        MakeDelta
        UploadStaging
        TriggerGHA["Trigger GitHub Actions"]
    end

    subgraph ci["CI"]
        GHA["publish-delta workflow"]
    end

    config --> ingest
    config --> collect_layer
    config --> persist
    config --> pipeline
    ingest --> persist
    collect_layer --> KIS_API
    collect_layer --> persist
    pipeline --> persist
    TriggerGHA --> GHA
```

---

## 3. 데이터 플로우 (전체 운영 흐름)

```mermaid
flowchart TD
    A["1. download_master"] --> B["2. parse_kospi_mst / parse_kosdaq_mst"]
    B --> C["3. build_universe"]
    C --> D{"수집 모드"}
    D -->|일일 증분| E["4a. incremental_job → collect_daily"]
    D -->|과거 백필| F["4b. backfill_job → collect_backfill"]
    E --> G["raw JSON + silver parquet 저장"]
    F --> G
    G --> H["collect_state / run_log 기록"]
    H --> I["5. make_delta"]
    I --> J["6. upload_to_staging"]
    J --> K["7. trigger_github_actions"]
    K --> L["GitHub Actions: Release 업로드 + data_manifest 갱신"]

    subgraph data_dirs["데이터 디렉터리"]
        M1["data/raw/master (mst zip)"]
        M2["data/raw/ohlcv (API 원문 JSON)"]
        M3["data/lake/master (파싱 parquet)"]
        M4["data/lake/silver/ohlcv_daily (종목·연도별 parquet)"]
        M5["data/delta (delta parquet + sha256 + json)"]
        M6["staging (업로드 대상)"]
    end

    A --> M1
    B --> M3
    C --> M3
    G --> M2
    G --> M4
    I --> M5
    J --> M6
```

---

## 4. 모듈 의존성 (코드 레벨)

```mermaid
flowchart TB
    subgraph common["src.common"]
        settings
        logger
        utils
    end

    subgraph clients["src.clients"]
        kis_client
    end

    subgraph storage["src.storage"]
        meta_store
        parquet_store
    end

    subgraph master["src.master"]
        download_master
        parse_kospi_mst
        parse_kosdaq_mst
        build_universe
    end

    subgraph collect["src.collect"]
        base_collect
        collect_daily
        collect_backfill
    end

    subgraph jobs["src.jobs"]
        incremental_job
        backfill_job
    end

    subgraph transform["src.transform"]
        make_delta
    end

    subgraph publish["src.publish"]
        upload_to_staging
        trigger_github_actions
    end

    settings --> kis_client
    settings --> download_master
    settings --> build_universe
    settings --> parquet_store
    settings --> meta_store
    settings --> make_delta
    settings --> upload_to_staging
    logger --> download_master
    logger --> build_universe
    logger --> collect_daily
    logger --> collect_backfill
    logger --> backfill_job
    logger --> make_delta
    logger --> upload_to_staging
    utils --> download_master
    utils --> make_delta

    kis_client --> collect_daily
    kis_client --> collect_backfill
    base_collect --> collect_daily
    base_collect --> collect_backfill
    meta_store --> build_universe
    meta_store --> collect_daily
    meta_store --> collect_backfill
    meta_store --> backfill_job
    meta_store --> make_delta
    parquet_store --> collect_daily
    parquet_store --> collect_backfill

    incremental_job --> collect_daily
    backfill_job --> collect_backfill

    make_delta --> meta_store
    trigger_github_actions --> make_delta
```

---

## 5. 모듈별 구현 요약

### 5.1 common (설정·로깅·유틸)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `settings.py` | 환경 설정 | `PROJECT_ROOT`, KIS API 키/URL, `STAGING_DIR`, `LOG_LEVEL`, `BACKFILL_CHUNK_DAYS` 등 `Settings` dataclass |
| `logger.py` | 로깅 | `get_logger(name)` — StreamHandler, 포맷, 레벨 설정 |
| `utils.py` | 유틸 | `sha256_file(path)`, `chunk_date_ranges(start, end, days)` |

### 5.2 clients (외부 API)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `kis_client.py` | KIS Open API 클라이언트 | `KISConfig` / `KISClient`: `issue_token()`, `get_token()`, `request_get()`, `get_daily_ohlcv(symbol, start_date, end_date)` — Bearer 토큰 갱신, TR ID 헤더 |

### 5.3 master (종목 마스터)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `download_master.py` | mst 다운로드 | KOSPI/KOSDAQ mst zip URL 다운로드 → 압축 해제 → `data/raw/master`, sha256 로깅 |
| `parse_kospi_mst.py` | 코스피 mst 파싱 | 고정폭 파싱: head(뒤 228자 제외) → symbol/std_code/name, tail → raw_tail, `data/lake/master/kospi_master.parquet` |
| `parse_kosdaq_mst.py` | 코스닥 mst 파싱 | 동일 구조, tail 222자 → `kosdaq_master.parquet` |
| `build_universe.py` | universe 생성 | kospi + kosdaq parquet 통합, SPAC 제외(옵션), DuckDB `universe` 테이블 replace, `universe.parquet` 저장 |

### 5.4 collect (일봉 수집)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `base_collect.py` | 정규화·검증 | `normalize_ohlcv(symbol, market, payload)` — output2/output → 공통 스키마; `validate_ohlcv(df)` — 중복·가격·OHLC 관계 검증 |
| `collect_daily.py` | 일일 증분 수집 | universe 로드 → 종목별 KIS 호출 → raw JSON 저장 → 정규화/검증 → symbol·연도별 parquet upsert → `collect_state`/`run_log` 기록 |
| `collect_backfill.py` | 백필 수집 | `collect_one()` 단일 종목 수집; `run_parallel(rows, start, end, workers)` — ThreadPoolExecutor 병렬 수집 |

### 5.5 jobs (수집 오케스트레이션)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `incremental_job.py` | 증분 수집 진입점 | `meta_store.ensure_tables()` → universe 로드 → `collect_daily.main()` 호출 |
| `backfill_job.py` | 백필 수집 진입점 | `ensure_tables()` → universe 로드 → `run_parallel()` → `meta_store.log_run()` |

### 5.6 storage (메타·파일 저장)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `meta_store.py` | DuckDB 메타 DB | `meta/meta.duckdb`: `ensure_tables()` (universe, collect_state, run_log, delta_history), `replace_universe()`, `load_universe()`, `upsert_collect_state()`, `log_run()`, `record_delta()` |
| `parquet_store.py` | raw/silver 파일 | `save_raw_json(symbol, payload)` → `data/raw/ohlcv/{date}/{symbol}.json`; `write_symbol_parquet(df)` → `data/lake/silver/ohlcv_daily/market={market}/symbol={symbol}/year={year}/data.parquet` (기존 파일 merge 후 upsert) |

### 5.7 transform (Delta 생성)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `make_delta.py` | Delta Parquet 생성 | 기간별 silver parquet 읽기(DuckDB `read_parquet`) → 단일 delta parquet + `.sha256` + `.json` manifest → `delta_history` 기록 |

### 5.8 publish (스테이징·릴리스 트리거)

| 파일 | 역할 | 주요 내용 |
|------|------|-----------|
| `upload_to_staging.py` | 스테이징 업로드 | `data/delta/{tag}.parquet/.sha256/.json` → `STAGING_DIR` 복사, 공개 URL 출력 |
| `trigger_github_actions.py` | 워크플로 디스패치 | manifest에서 sha256/min_date/max_date 읽어 `gh workflow run` 입력으로 전달 (tag, delta_url, sha256 등) |

---

## 6. 메타데이터 스키마 (DuckDB)

```mermaid
erDiagram
    universe {
        TEXT symbol PK
        TEXT std_code
        TEXT name
        TEXT market
        TEXT asset_type
        DATE listing_date
        BOOLEAN is_etf
        BOOLEAN is_spac
        BOOLEAN is_trading_halt
        BOOLEAN is_admin_issue
        BOOLEAN is_warning
        BOOLEAN is_active
        TIMESTAMP updated_at
        TEXT raw_tail
    }

    collect_state {
        TEXT symbol PK
        TEXT timeframe PK
        DATE last_success_date
        TIMESTAMP last_attempt_at
        INTEGER retry_count
        TEXT last_error
        TIMESTAMP updated_at
    }

    run_log {
        TEXT run_id
        TEXT job_name
        TIMESTAMP started_at
        TIMESTAMP ended_at
        TEXT status
        INTEGER total_symbols
        INTEGER success_symbols
        INTEGER failed_symbols
        TEXT note
    }

    delta_history {
        TEXT delta_tag PK
        TEXT file_name
        TEXT file_path
        TEXT sha256
        INTEGER row_count
        DATE min_date
        DATE max_date
        TIMESTAMP created_at
    }
```

---

## 7. GitHub Actions (publish-delta)

```mermaid
flowchart TD
    A["workflow_dispatch (tag, delta_url, sha256, min_date, max_date, asset_name)"] --> B["Checkout / Git 설정"]
    B --> C["Acquire lock (LOCK.json)"]
    C --> D["Download delta from staging (curl)"]
    D --> E["Verify sha256"]
    E --> F["Create release if not exists (gh release create)"]
    F --> G["Upload release asset (gh release upload)"]
    G --> H["Update data_manifest.json (latest_delta, history.delta_tags)"]
    H --> I["Commit & push manifest"]
    I --> J["Release lock (always)"]
```

---

## 8. 실행 순서 요약

| 단계 | 명령/모듈 | 설명 |
|------|-----------|------|
| 1 | `download_master` | KOSPI/KOSDAQ mst zip 다운로드 및 압축 해제 |
| 2 | `parse_kospi_mst`, `parse_kosdaq_mst` | mst 파싱 → lake/master parquet |
| 3 | `build_universe` | master 통합 → universe 테이블 및 universe.parquet |
| 4a | `incremental_job` | 일일 증분 수집 (collect_daily) |
| 4b | `backfill_job` | 장기 백필 수집 (collect_backfill, 병렬) |
| 5 | `make_delta` | 기간 지정 silver → delta parquet + manifest + delta_history |
| 6 | `upload_to_staging` | delta 파일들 스테이징 디렉터리로 복사 |
| 7 | `trigger_github_actions` | publish-delta 워크플로 실행 → Release 및 data_manifest 갱신 |

이 문서는 프로젝트 폴더 구성, 레이어 구조, 데이터 플로우, 모듈 의존성, 모듈별 구현 요약, 메타 스키마, CI 흐름을 머메이드 다이어그램과 표로 정리한 것입니다.
