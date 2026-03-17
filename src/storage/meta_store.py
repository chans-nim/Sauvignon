from __future__ import annotations
import time
import duckdb
import pandas as pd
from src.common.settings import settings

META_DB = settings.project_root / "meta" / "meta.duckdb"
META_DB.parent.mkdir(parents=True, exist_ok=True)

CONNECT_RETRIES = 5
CONNECT_RETRY_DELAY_SEC = 2.0


def connect():
    last_err = None
    for attempt in range(CONNECT_RETRIES):
        try:
            return duckdb.connect(META_DB.as_posix())
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "already open" in msg or "locked" in msg or "another process" in msg or "cannot open file" in msg:
                if attempt < CONNECT_RETRIES - 1:
                    time.sleep(CONNECT_RETRY_DELAY_SEC * (attempt + 1))
                    continue
            raise
    if last_err:
        raise last_err

def ensure_tables() -> None:
    con = connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            symbol TEXT PRIMARY KEY,
            std_code TEXT,
            name TEXT,
            market TEXT,
            asset_type TEXT,
            listing_date DATE,
            is_etf BOOLEAN,
            is_spac BOOLEAN,
            is_trading_halt BOOLEAN,
            is_admin_issue BOOLEAN,
            is_warning BOOLEAN,
            is_active BOOLEAN,
            updated_at TIMESTAMP,
            raw_tail TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS collect_state (
            symbol TEXT,
            timeframe TEXT,
            last_success_date DATE,
            last_attempt_at TIMESTAMP,
            retry_count INTEGER,
            last_error TEXT,
            updated_at TIMESTAMP,
            PRIMARY KEY(symbol, timeframe)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_id TEXT,
            job_name TEXT,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            status TEXT,
            total_symbols INTEGER,
            success_symbols INTEGER,
            failed_symbols INTEGER,
            note TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS delta_history (
            delta_tag TEXT PRIMARY KEY,
            file_name TEXT,
            file_path TEXT,
            sha256 TEXT,
            row_count INTEGER,
            min_date DATE,
            max_date DATE,
            created_at TIMESTAMP
        )
    """)
    con.close()

def replace_universe(df: pd.DataFrame) -> None:
    con = connect()
    ensure_tables()
    con.register("universe_df", df[[
        "symbol","std_code","name","market","asset_type","listing_date","is_etf","is_spac",
        "is_trading_halt","is_admin_issue","is_warning","is_active","updated_at","raw_tail"
    ]])
    con.execute("DELETE FROM universe")
    con.execute("INSERT INTO universe SELECT * FROM universe_df")
    con.close()

def load_universe(limit: int | None = None) -> pd.DataFrame:
    con = connect()
    sql = "SELECT symbol,name,market FROM universe WHERE is_active = TRUE ORDER BY market,symbol"
    if limit:
        sql += f" LIMIT {int(limit)}"
    df = con.execute(sql).fetchdf()
    con.close()
    return df


def load_failed_symbols(timeframe: str = "1d") -> pd.DataFrame:
    """
    collect_state에서 last_error가 있는 종목만 반환한다.
    반환 컬럼: symbol, name, market (universe와 동일).
    """
    con = connect()
    df = con.execute("""
        SELECT u.symbol, u.name, u.market
        FROM universe u
        INNER JOIN collect_state c ON u.symbol = c.symbol AND c.timeframe = ?
        WHERE u.is_active = TRUE AND c.last_error IS NOT NULL
        ORDER BY u.market, u.symbol
    """, [timeframe]).fetchdf()
    con.close()
    return df

def upsert_collect_state(symbol: str, ok: bool, last_success_date: str | None, error: str | None) -> None:
    con = connect()
    now = pd.Timestamp.now()
    if ok:
        con.execute("""
            INSERT INTO collect_state AS t
            VALUES (?, '1d', ?, ?, 0, NULL, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                last_success_date=excluded.last_success_date,
                last_attempt_at=excluded.last_attempt_at,
                retry_count=0,
                last_error=NULL,
                updated_at=excluded.updated_at
        """, [symbol, last_success_date, now, now])
    else:
        prev = con.execute("SELECT COALESCE(retry_count, 0) FROM collect_state WHERE symbol=? AND timeframe='1d'", [symbol]).fetchone()
        retry_count = (prev[0] if prev else 0) + 1
        con.execute("""
            INSERT INTO collect_state AS t
            VALUES (?, '1d', NULL, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                last_attempt_at=excluded.last_attempt_at,
                retry_count=excluded.retry_count,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
        """, [symbol, now, retry_count, error, now])
    con.close()

def log_run(run_id: str, job_name: str, status: str, total: int, success: int, failed: int, note: str = "") -> None:
    con = connect()
    con.execute(
        "INSERT INTO run_log VALUES (?, ?, now(), now(), ?, ?, ?, ?, ?)",
        [run_id, job_name, status, total, success, failed, note],
    )
    con.close()

def record_delta(tag: str, file_name: str, file_path: str, sha256: str, row_count: int, min_date, max_date) -> None:
    con = connect()
    con.execute("""
        INSERT INTO delta_history AS t
        VALUES (?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT(delta_tag) DO UPDATE SET
            file_name=excluded.file_name,
            file_path=excluded.file_path,
            sha256=excluded.sha256,
            row_count=excluded.row_count,
            min_date=excluded.min_date,
            max_date=excluded.max_date,
            created_at=excluded.created_at
    """, [tag, file_name, file_path, sha256, row_count, min_date, max_date])
    con.close()
