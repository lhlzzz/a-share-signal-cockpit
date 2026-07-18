# DB-T1: PostgreSQL 表结构 + Docker Compose + 项目骨架

## 目标
为 xiaogu 建立数据库基础设施：PostgreSQL + Docker Compose（Bind Mount）+ 数据库初始化脚本。

## 工作目录
/workspace/hermes-workspaces/xiaogu

## 需要创建的文件

### 1. docker-compose.yml（项目根目录）
```yaml
version: "3.9"

services:
  db:
    image: postgres:16
    volumes:
      - ./postgres-data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: xiaogu
      POSTGRES_USER: xiaogu
      POSTGRES_PASSWORD: xiaogu
    ports:
      - "5432:5432"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U xiaogu"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    build: .
    volumes:
      - ./data:/app/data
      - .:/app
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://xiaogu:xiaogu@db:5432/xiaogu
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    command: uvicorn xiaogu_api:app --host 0.0.0.0 --port 8000 --reload

  scheduler:
    build: .
    volumes:
      - ./data:/app/data
      - .:/app
    environment:
      DATABASE_URL: postgresql://xiaogu:xiaogu@db:5432/xiaogu
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    command: python3 xiaogu_scheduler.py

volumes: {}
```

### 2. Dockerfile（项目根目录）
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "xiaogu_scheduler.py"]
```

### 3. requirements.txt（若不存在则新建，若存在则追加缺失依赖）
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.9
apscheduler>=3.10.4
pyarrow>=16.0.0
pandas>=2.2.0
alembic>=1.13.0
python-dotenv>=1.0.0
```

### 4. scripts/xiaogu_db_init.sql（数据库初始化 SQL）
```sql
-- picks: 每日出票记录（替代 jsonl ledger）
CREATE TABLE IF NOT EXISTS picks (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10),
    decision VARCHAR(20) NOT NULL,
    final_score FLOAT,
    blockers JSONB DEFAULT '[]',
    features JSONB DEFAULT '{}',
    source_layers JSONB DEFAULT '[]',
    rule_version VARCHAR(50),
    scan_dir VARCHAR(500),
    dry_run BOOLEAN DEFAULT TRUE,
    paper_only BOOLEAN DEFAULT TRUE,
    no_trade BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_picks_trade_date ON picks(trade_date);
CREATE INDEX IF NOT EXISTS idx_picks_symbol ON picks(symbol);
CREATE INDEX IF NOT EXISTS idx_picks_decision ON picks(decision);

-- returns: T+1/2/3 收益回填
CREATE TABLE IF NOT EXISTS returns (
    id SERIAL PRIMARY KEY,
    pick_id INT REFERENCES picks(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    t1_return FLOAT,
    t2_return FLOAT,
    t3_return FLOAT,
    is_limit_up BOOLEAN GENERATED ALWAYS AS (t1_return >= 0.095) STORED,
    filled_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_returns_trade_date ON returns(trade_date);
CREATE INDEX IF NOT EXISTS idx_returns_symbol ON returns(symbol);

-- scan_sessions: 扫描会话元数据
CREATE TABLE IF NOT EXISTS scan_sessions (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    scan_time TIMESTAMPTZ NOT NULL,
    cdp_url VARCHAR(100),
    quotes_count INT DEFAULT 0,
    scored_count INT DEFAULT 0,
    passed_count INT DEFAULT 0,
    scan_dir VARCHAR(500),
    status VARCHAR(20) DEFAULT 'completed',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- signal_effectiveness: 每日信号分析快照
CREATE TABLE IF NOT EXISTS signal_effectiveness (
    id SERIAL PRIMARY KEY,
    analysis_date DATE NOT NULL,
    signal_key VARCHAR(50) NOT NULL,
    present_count INT DEFAULT 0,
    limit_up_rate FLOAT DEFAULT 0.0,
    avg_t1_return FLOAT DEFAULT 0.0,
    weight_suggestion VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(analysis_date, signal_key)
);
```

### 5. xiaogu_db.py（数据库连接和 ORM 工具）
```python
"""Database connection and helpers for xiaogu."""
import os
from contextlib import contextmanager
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(sql_path: str = "scripts/xiaogu_db_init.sql") -> None:
    """Run init SQL to create tables."""
    with open(sql_path, "r") as f:
        sql = f.read()
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()


def insert_pick(
    trade_date: date,
    symbol: str,
    decision: str,
    final_score: Optional[float],
    blockers: List[str],
    features: Dict[str, Any],
    source_layers: List[str],
    rule_version: str,
    scan_dir: str = "",
    dry_run: bool = True,
) -> int:
    """Insert a pick record, return its id."""
    with get_db() as db:
        result = db.execute(
            text("""
                INSERT INTO picks
                    (trade_date, symbol, decision, final_score, blockers,
                     features, source_layers, rule_version, scan_dir, dry_run)
                VALUES
                    (:trade_date, :symbol, :decision, :final_score, :blockers::jsonb,
                     :features::jsonb, :source_layers::jsonb, :rule_version, :scan_dir, :dry_run)
                RETURNING id
            """),
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "decision": decision,
                "final_score": final_score,
                "blockers": str(blockers).replace("'", '"'),
                "features": "{}",
                "source_layers": str(source_layers).replace("'", '"'),
                "rule_version": rule_version,
                "scan_dir": scan_dir,
                "dry_run": dry_run,
            }
        )
        row = result.fetchone()
        return row[0] if row else -1


def upsert_return(
    trade_date: date,
    symbol: str,
    pick_id: Optional[int],
    t1_return: Optional[float] = None,
    t2_return: Optional[float] = None,
    t3_return: Optional[float] = None,
) -> None:
    """Upsert a return record."""
    import json
    with get_db() as db:
        db.execute(
            text("""
                INSERT INTO returns (pick_id, trade_date, symbol, t1_return, t2_return, t3_return)
                VALUES (:pick_id, :trade_date, :symbol, :t1_return, :t2_return, :t3_return)
                ON CONFLICT (trade_date, symbol)
                DO UPDATE SET
                    t1_return = COALESCE(EXCLUDED.t1_return, returns.t1_return),
                    t2_return = COALESCE(EXCLUDED.t2_return, returns.t2_return),
                    t3_return = COALESCE(EXCLUDED.t3_return, returns.t3_return),
                    filled_at = NOW()
            """),
            {
                "pick_id": pick_id,
                "trade_date": trade_date,
                "symbol": symbol,
                "t1_return": t1_return,
                "t2_return": t2_return,
                "t3_return": t3_return,
            }
        )


def insert_scan_session(
    trade_date: date,
    scan_time: Any,
    cdp_url: str,
    quotes_count: int,
    scored_count: int,
    passed_count: int,
    scan_dir: str,
) -> int:
    with get_db() as db:
        result = db.execute(
            text("""
                INSERT INTO scan_sessions
                    (trade_date, scan_time, cdp_url, quotes_count, scored_count, passed_count, scan_dir)
                VALUES (:trade_date, :scan_time, :cdp_url, :quotes_count, :scored_count, :passed_count, :scan_dir)
                RETURNING id
            """),
            {
                "trade_date": trade_date,
                "scan_time": scan_time,
                "cdp_url": cdp_url,
                "quotes_count": quotes_count,
                "scored_count": scored_count,
                "passed_count": passed_count,
                "scan_dir": scan_dir,
            }
        )
        row = result.fetchone()
        return row[0] if row else -1
```

### 6. .gitignore 追加（在已有内容后面加）
```
postgres-data/
*.pyc
__pycache__/
.env
```

## 验收标准
1. `python3 -m py_compile xiaogu_db.py` 无错
2. `docker-compose config` 无错（需要 docker-compose 可用时验证，否则跳过）
3. `scripts/xiaogu_db_init.sql` 文件存在且包含 4 个 CREATE TABLE 语句
4. `python3 -m pytest tests/ -x -q` 仍然全部通过（不得破坏现有测试）

## 禁止修改
- `forward_paper_ledger_v0_1.jsonl`
- `xiaogu_forward_d1_1450_runner_v0_1.py`
- `xiaogu_eastmoney_web_tabs_scan_v0_1.py`
- 任何现有测试
