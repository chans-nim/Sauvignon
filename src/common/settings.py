from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    project_root: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
    # 실전 투자용 KIS Open API 자격 증명
    kis_app_key: str = os.getenv("KIS_APP_KEY", "")
    kis_app_secret: str = os.getenv("KIS_APP_SECRET", "")
    # 모의 투자(가상서버, vps)용 자격 증명 (있을 경우 사용)
    kis_paper_app_key: str = os.getenv("KIS_PAPER_APP_KEY", "")
    kis_paper_app_secret: str = os.getenv("KIS_PAPER_APP_SECRET", "")
    # 공통 엔드포인트 설정
    kis_base_url: str = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    kis_token_url: str = os.getenv("KIS_TOKEN_URL", "/oauth2/tokenP")
    kis_daily_price_url: str = os.getenv("KIS_DAILY_PRICE_URL", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice")
    kis_daily_price_tr_id: str = os.getenv("KIS_DAILY_PRICE_TR_ID", "FHKST03010100")
    # 기본 환경 (prod: 실전, vps: 모의)
    kis_env: str = os.getenv("KIS_ENV", "prod")
    # 스테이징/로깅 관련
    staging_dir: Path = Path(os.getenv("STAGING_DIR", "./staging")).resolve()
    staging_base_url: str = os.getenv("STAGING_BASE_URL", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # KIS 일봉 API는 1회 요청당 약 100건 제한. 100일 청크면 거래일 ~60일 이하로 전건 수집 가능.
    backfill_chunk_days: int = int(os.getenv("BACKFILL_CHUNK_DAYS", "100"))


settings = Settings()
