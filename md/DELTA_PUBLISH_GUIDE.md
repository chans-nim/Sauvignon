# Delta / Staging / GitHub Actions 추가 가이드

## 추가 파일
- `src/transform/make_delta.py`
- `src/publish/upload_to_staging.py`
- `src/publish/trigger_github_actions.py`

## 역할 요약

### make_delta.py
Silver parquet 전체에서 날짜 구간을 읽어 delta 파일을 만듭니다.
출력:
- `data/delta/<tag>.parquet`
- `data/delta/<tag>.sha256`
- `data/delta/<tag>.json`

또한 DuckDB의 `delta_history` 테이블을 갱신합니다.

### upload_to_staging.py
delta 파일/sha256/manifest를 staging 디렉터리로 복사합니다.
기본은 파일시스템 staging 방식입니다.
환경변수:
- `STAGING_DIR`
- `STAGING_BASE_URL`

### trigger_github_actions.py
delta manifest를 읽어 `gh workflow run` 명령을 생성합니다.
기본은 dry-run이며 `--run` 옵션 때 실제 실행합니다.

## 사용 예시

### 1) delta 생성
```bash
python -m src.transform.make_delta --start-date 2025-12-20 --end-date 2025-12-31 --tag data-delta-20251231-1800
```

### 2) staging 업로드
```bash
python -m src.publish.upload_to_staging --tag data-delta-20251231-1800
```

### 3) GitHub Actions 트리거
```bash
python -m src.publish.trigger_github_actions \
  --tag data-delta-20251231-1800 \
  --delta-url "file:///absolute/path/to/staging/data-delta-20251231-1800.parquet"
```

실제 실행:
```bash
python -m src.publish.trigger_github_actions \
  --tag data-delta-20251231-1800 \
  --delta-url "https://your-staging.example.com/data-delta-20251231-1800.parquet" \
  --run
```

## 다음 추천
- `run_log` 테이블 추가
- changed symbols 기반 delta 최적화
- parquet.zst 압축 적용
- S3/R2 업로드 버전으로 확장
- GitHub Actions workflow yaml과 연결
