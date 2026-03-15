# KIS Data Platform Skeleton 내부 구현 문서

## 목적
이 코드는 KIS 기반 데이터 수집 시스템의 최소 실행 뼈대입니다.

핵심 목표:
1. mst 다운로드
2. mst 파싱 및 universe 생성
3. KIS 일봉 수집
4. raw/silver 저장
5. collect_state 상태 관리

## 디렉터리별 역할

### `src/master`
- `download_master.py`: KOSPI/KOSDAQ mst zip 다운로드 및 압축해제
- `parse_kospi_mst.py`: 코스피 mst 최소 파싱
- `parse_kosdaq_mst.py`: 코스닥 mst 최소 파싱
- `build_universe.py`: 파싱 결과 통합, 필터링, DuckDB 저장

### `src/clients`
- `kis_client.py`: 토큰 발급, 공통 GET 요청, 일봉 조회 helper

### `src/collect`
- `collect_daily.py`: universe를 읽고 종목별 일봉 수집, raw 저장, parquet upsert

### `meta`
- `meta.duckdb`: universe, collect_state 같은 운영 메타 저장

### `data`
- `data/raw/master`: mst 원본
- `data/raw/ohlcv`: API 원문 JSON
- `data/lake/master`: 파싱된 master parquet
- `data/lake/silver`: 정규화된 일봉 parquet

## 파일별 구현 설명

## 1. download_master.py
### 역할
- mst zip 다운로드
- 압축 해제
- sha256 계산

### 구현 의도
- 다운로드와 무결성 체크를 먼저 분리
- 나중에 master_status 기록으로 쉽게 확장 가능

## 2. parse_kospi_mst.py / parse_kosdaq_mst.py
### 역할
- mst를 최소 컬럼으로 파싱

### 파싱 방식
- 코스피: 뒤 228자 tail 분리
- 코스닥: 뒤 222자 tail 분리
- head에서 symbol/std_code/name 추출
- tail 전체는 `raw_tail`로 저장

### 구현 의도
처음부터 전체 상세 필드를 모두 매핑하지 않고, 원본 tail을 남겨 후속 확장을 쉽게 함

## 3. build_universe.py
### 역할
- KOSPI/KOSDAQ master parquet 통합
- 기본 투자 대상 universe 생성
- DuckDB `universe` 테이블 갱신

### 현재 정책
- SPAC 기본 제외
- ETF는 아직 미포함
- 거래정지/관리종목/경고는 추후 tail 상세 파싱 후 반영 예정

## 4. kis_client.py
### 역할
- access token 발급
- 공통 header 생성
- GET 요청
- 기간별시세 요청 helper 제공

### 조정 필요 항목
- 엔드포인트 URL
- TR ID
- 응답 JSON 키
- 시장 구분 코드

## 5. collect_daily.py
### 역할
- universe 로드
- 종목별 KIS 호출
- raw JSON 저장
- OHLCV 정규화
- 데이터 검증
- 종목/연도 단위 parquet upsert
- collect_state 저장

### 내부 함수
- `ensure_collect_state_table()`: 상태 테이블 생성
- `load_universe()`: active universe 조회
- `save_raw_json()`: 응답 원문 저장
- `normalize_ohlcv()`: 응답을 공통 스키마로 변환
- `validate_ohlcv()`: 가격/거래량 검증
- `write_symbol_parquet()`: symbol/year 단위 저장
- `upsert_collect_state()`: 성공/실패 기록

## 실행 순서
```bash
python -m src.master.download_master
python -m src.master.parse_kospi_mst
python -m src.master.parse_kosdaq_mst
python -m src.master.build_universe
python -m src.collect.collect_daily --start-date 2025-01-01 --end-date 2025-12-31 --limit 10
```

## 현재 한계
- retry/backoff 미구현
- run_log 미구현
- delta 생성 미구현
- staging 업로드 미구현
- GitHub Actions 트리거 미구현
- mst tail 상세 필드 미매핑

## 다음 확장 우선순위
1. `normalize_ohlcv.py`, `validate_ohlcv.py` 분리
2. `make_delta.py` 추가
3. `upload_to_staging.py` 추가
4. `trigger_github_actions.py` 추가
5. `run_log` 및 scheduler 연결
