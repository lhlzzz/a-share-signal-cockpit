#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pgvector-backed similar-case store for paper picks and top candidates.

True vector stack:
- Storage: Postgres `vector` extension (pgvector) + optional HNSW cosine index
- Retrieval: `embedding <=> query` cosine distance
- Embedding backends:
  1) **neural** (explicit opt-in): sentence-transformers multilingual MiniLM
     (384-d, L2-normalized). Real semantic vectors for Chinese case text.
  2) **structured** (fallback): structured hybrid v2 (numeric channels + multi-probe
     hashed bag-of-words, 64-d). Deterministic, no model download.

Env:
  XIAOGU_EMBED_BACKEND=neural|structured|auto  (default: structured)
  XIAOGU_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  XIAOGU_CASE_EMBED_DIM= only used by structured backend (default 64)
  XIAOGU_EMBED_ALLOW_NETWORK=1 to permit model download; default is offline/local-only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

TABLE = 'pick_case_embeddings'
STRUCTURED_DIM = int(os.environ.get('XIAOGU_CASE_EMBED_DIM', '64'))
NUMERIC_SLOTS = 8
NEURAL_MODEL_DEFAULT = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
_TOKEN_RE = re.compile(r'[\w\u4e00-\u9fff]+', re.UNICODE)

_backend_lock = threading.Lock()
_resolved_backend: Optional[str] = None
_neural_model = None
_neural_dim: Optional[int] = None
_neural_error: Optional[str] = None


def _requested_backend() -> str:
    raw = str(os.environ.get('XIAOGU_EMBED_BACKEND', 'structured') or 'structured').strip().lower()
    if raw in ('neural', 'structured', 'auto'):
        return raw
    return 'auto'


def neural_model_name() -> str:
    return str(os.environ.get('XIAOGU_EMBED_MODEL', NEURAL_MODEL_DEFAULT) or NEURAL_MODEL_DEFAULT)


def _allow_network_fetch() -> bool:
    raw = str(os.environ.get('XIAOGU_EMBED_ALLOW_NETWORK', '0') or '0').strip().lower()
    if raw in {'0', 'false', 'no', 'off'}:
        return False
    if raw in {'1', 'true', 'yes', 'on'}:
        return True
    return True


def _load_neural_model() -> Any:
    """Lazy-load sentence-transformers model. Thread-safe."""
    global _neural_model, _neural_dim, _neural_error
    if _neural_model is not None:
        return _neural_model
    with _backend_lock:
        if _neural_model is not None:
            return _neural_model
        try:
            allow_network = _allow_network_fetch()
            if not allow_network:
                os.environ.setdefault('HF_HUB_OFFLINE', '1')
                os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
            from sentence_transformers import SentenceTransformer
            name = neural_model_name()
            try:
                model = SentenceTransformer(name, local_files_only=not allow_network)
            except TypeError:
                # Older sentence-transformers releases may not accept local_files_only.
                # Offline env vars above still keep the load local-only by default.
                model = SentenceTransformer(name)
            dim = None
            if hasattr(model, 'get_embedding_dimension'):
                dim = int(model.get_embedding_dimension())
            elif hasattr(model, 'get_sentence_embedding_dimension'):
                dim = int(model.get_sentence_embedding_dimension())
            if not dim:
                probe = model.encode(['probe'], normalize_embeddings=True)
                dim = int(len(probe[0]))
            _neural_model = model
            _neural_dim = dim
            _neural_error = None
            return _neural_model
        except Exception as exc:
            _neural_error = f'{type(exc).__name__}: {exc}'
            _neural_model = None
            _neural_dim = None
            raise


def neural_available() -> bool:
    try:
        _load_neural_model()
        return True
    except Exception:
        return False


def resolve_embed_backend() -> str:
    """Return 'neural' or 'structured' for this process."""
    global _resolved_backend
    if _resolved_backend in ('neural', 'structured'):
        return _resolved_backend
    req = _requested_backend()
    if req == 'structured':
        _resolved_backend = 'structured'
        return _resolved_backend
    if req == 'neural':
        _load_neural_model()  # raise if unavailable
        _resolved_backend = 'neural'
        return _resolved_backend
    # auto
    if neural_available():
        _resolved_backend = 'neural'
    else:
        _resolved_backend = 'structured'
    return _resolved_backend


def get_embed_dim() -> int:
    backend = resolve_embed_backend()
    if backend == 'neural':
        _load_neural_model()
        return int(_neural_dim or 384)
    return STRUCTURED_DIM


def embed_method_name() -> str:
    backend = resolve_embed_backend()
    if backend == 'neural':
        model = neural_model_name().split('/')[-1]
        return f'neural_{model}'
    return 'structured_hybrid_v2'


# Back-compat module attributes (tests / exporters may import these).
# Note: EMBED_DIM/EMBED_METHOD resolve lazily via property-like accessors below.
def _sync_public_constants() -> None:
    global EMBED_DIM, EMBED_METHOD
    try:
        EMBED_DIM = get_embed_dim()
        EMBED_METHOD = embed_method_name()
    except Exception:
        EMBED_DIM = STRUCTURED_DIM
        EMBED_METHOD = 'structured_hybrid_v2'


# Initialize with structured defaults; refreshed after first successful resolve.
EMBED_DIM = STRUCTURED_DIM
EMBED_METHOD = 'structured_hybrid_v2'


def tokenize(text: str) -> List[str]:
    raw = [t.lower() for t in _TOKEN_RE.findall(text or '') if t.strip()]
    bigrams: List[str] = []
    for tok in raw:
        if len(tok) >= 2 and all('\u4e00' <= ch <= '\u9fff' for ch in tok):
            for i in range(len(tok) - 1):
                bigrams.append(tok[i : i + 2])
    return raw + bigrams


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _multi_probe_hash(token: str, dim: int, start: int, probes: int = 3) -> List[Tuple[int, float]]:
    h = hashlib.sha256(token.encode('utf-8')).digest()
    out: List[Tuple[int, float]] = []
    span = max(1, dim - start)
    for j in range(probes):
        idx = start + (int.from_bytes(h[j * 4 : (j + 1) * 4], 'little') % span)
        sign = 1.0 if h[12 + j] % 2 == 0 else -1.0
        weight = 1.0 + (h[15 + j] / 255.0)
        out.append((idx, sign * weight * (1.0 - 0.08 * j)))
    return out


def embed_text_structured(
    text: str,
    dim: int = STRUCTURED_DIM,
    numeric: Optional[Sequence[float]] = None,
) -> List[float]:
    """Deterministic structured-hybrid embedding (fallback)."""
    dim = int(dim or STRUCTURED_DIM)
    vec = [0.0] * dim
    start = min(NUMERIC_SLOTS, dim)
    if numeric:
        for i, val in enumerate(list(numeric)[:start]):
            try:
                vec[i] = float(val)
            except (TypeError, ValueError):
                vec[i] = 0.0
    tokens = tokenize(text)
    if not tokens and not numeric:
        return vec
    for pos, tok in enumerate(tokens):
        pos_w = 1.15 if pos < 6 else 1.0
        for idx, w in _multi_probe_hash(tok, dim, start=start, probes=3):
            vec[idx] += w * pos_w
    return _l2_normalize(vec)


def _numeric_as_text(
    *,
    score: Any = None,
    features: Optional[Dict[str, Any]] = None,
    evidence_card: Optional[Dict[str, Any]] = None,
) -> str:
    """Serialize key numerics into text so neural models can see domain magnitudes."""
    feat = features or {}
    card = evidence_card or {}
    bits: List[str] = []

    def add(label: str, *keys: str, scale: str = '') -> None:
        for k in keys:
            if feat.get(k) is not None:
                try:
                    bits.append(f'{label}={float(feat.get(k)):.4f}{scale}')
                    return
                except (TypeError, ValueError):
                    pass
        if label == 'score' and score is not None:
            try:
                bits.append(f'score={float(score):.4f}')
            except (TypeError, ValueError):
                pass

    add('score', 'final_score', 'score')
    add('fund', 'fund_flow_momentum')
    add('theme', 'main_theme_core_score', 'main_theme_alignment_score')
    add('sector', 'sector_opportunity_score')
    add('pct', 'signal_pct', 'pct_chg')
    add('rank', 'rank')
    return ' '.join(bits)


def embed_text_neural(text: str) -> List[float]:
    model = _load_neural_model()
    # Empty text still needs a stable vector.
    payload = (text or ' ').strip() or 'empty'
    vec = model.encode([payload], normalize_embeddings=True)[0]
    return [round(float(x), 6) for x in vec]


def embed_text(
    text: str,
    dim: Optional[int] = None,
    numeric: Optional[Sequence[float]] = None,
) -> List[float]:
    """Public embed API. Neural by default when available; structured otherwise.

    `dim` is ignored for neural (model-native). For structured, defaults to STRUCTURED_DIM.
    `numeric` only applies to structured backend; for neural, callers should fold numbers
    into text via case_text_from_pick / _numeric_as_text.
    """
    backend = resolve_embed_backend()
    _sync_public_constants()
    if backend == 'neural':
        return embed_text_neural(text)
    return embed_text_structured(text, dim=int(dim or STRUCTURED_DIM), numeric=numeric)


def _numeric_features_from_payload(
    *,
    score: Any = None,
    features: Optional[Dict[str, Any]] = None,
    evidence_card: Optional[Dict[str, Any]] = None,
) -> List[float]:
    feat = features or {}
    card = evidence_card or {}

    def f(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            if feat.get(k) is not None:
                try:
                    return float(feat.get(k))
                except (TypeError, ValueError):
                    pass
        return default

    score_v = 0.0
    try:
        score_v = float(score if score is not None else feat.get('final_score') or feat.get('score') or 0.0)
    except (TypeError, ValueError):
        score_v = 0.0
    fund = f('fund_flow_momentum', default=0.0)
    theme = f('main_theme_core_score', 'main_theme_alignment_score', default=0.0)
    sector = f('sector_opportunity_score', default=0.0)
    pct = f('signal_pct', 'pct_chg', default=0.0)
    continuation = f('continuation_gene_score', default=0.0)
    t1 = f('t1_return', default=0.0)
    rank = f('rank', default=50.0)
    return [
        max(-1.0, min(1.0, score_v / 100.0)),
        max(-1.0, min(1.0, fund)),
        max(-1.0, min(1.0, theme)),
        max(-1.0, min(1.0, sector)),
        max(-1.0, min(1.0, pct / 10.0)),
        max(-1.0, min(1.0, continuation)),
        max(-1.0, min(1.0, t1)),
        max(0.0, min(1.0, 1.0 - (rank / 50.0))),
    ]


def case_text_from_pick(
    *,
    symbol: str,
    name: str,
    decision: str,
    score: Any,
    evidence_card: Optional[Dict[str, Any]] = None,
    features: Optional[Dict[str, Any]] = None,
    reason: str = '',
) -> str:
    parts: List[str] = [symbol, name, decision, str(score or ''), reason]
    card = evidence_card or {}
    for key in ('announcements', 'news', 'fund_flow', 'main_theme', 'risks'):
        items = card.get(key) or []
        if isinstance(items, list):
            parts.extend(str(x) for x in items[:4])
    feat = features or {}
    for key in (
        'industry', 'sector', 'predicted_sector', 'main_theme',
        'setup_type', 'candidate_stage',
    ):
        if feat.get(key):
            parts.append(str(feat.get(key)))
    tags = feat.get('sector_opportunity_tags') or feat.get('theme_tags') or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags[:8])
    for key in ('selection_outcome', 'not_selected_reason', 'ticket_reason'):
        val = feat.get(key)
        if val:
            parts.append(str(val)[:160])
    # Domain numerics as text — critical for neural backend (no separate numeric channel).
    parts.append(_numeric_as_text(score=score, features=feat, evidence_card=card))
    return ' '.join(str(p) for p in parts if p)


def _vector_literal(vec: Sequence[float]) -> str:
    return '[' + ','.join(f'{float(x):.6f}' for x in vec) + ']'


def _current_table_embed_dim(db, text) -> Optional[int]:
    try:
        dim = db.execute(text(f"""
            SELECT vector_dims(embedding)
            FROM {TABLE}
            WHERE embedding IS NOT NULL
            LIMIT 1
        """)).scalar()
        if dim is not None:
            return int(dim)
    except Exception:
        pass
    try:
        fmt = db.execute(text("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :table AND a.attname = 'embedding' AND n.nspname = 'public'
        """), {'table': TABLE}).scalar()
        # format like vector(64) or vector(384)
        if fmt and '(' in str(fmt):
            return int(str(fmt).split('(')[1].split(')')[0])
    except Exception:
        pass
    return None


def migrate_embedding_dimension(target_dim: int) -> Dict[str, Any]:
    """Refuse dimension changes so existing embeddings cannot be destroyed."""
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': str(exc)}
    target_dim = int(target_dim)
    with get_db() as db:
        current = _current_table_embed_dim(db, text)
        if current == target_dim:
            return {'status': 'OK', 'action': 'noop', 'dim': target_dim}
        return {
            'status': 'REFUSED',
            'action': 'preserve_existing_embeddings',
            'from_dim': current,
            'to_dim': target_dim,
            'note': 'dimension migration requires an explicit non-destructive migration',
        }


def ensure_case_embedding_table() -> Dict[str, Any]:
    """Create pick_case_embeddings if missing; align embedding dim to active backend."""
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': str(exc)}

    try:
        target_dim = get_embed_dim()
        method = embed_method_name()
    except Exception as exc:
        # Hard-fail neural when forced; auto already falls back before here.
        return {'status': 'FAILED', 'error': f'embed_backend_unavailable: {exc}'}

    ddl = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        id SERIAL PRIMARY KEY,
        trade_date DATE NOT NULL,
        symbol VARCHAR(10) NOT NULL,
        decision VARCHAR(20) NOT NULL DEFAULT 'PAPER_PICK',
        production_run_id VARCHAR(128),
        stock_name VARCHAR(30),
        final_score FLOAT,
        case_text TEXT NOT NULL,
        embedding vector({target_dim}),
        metadata JSONB DEFAULT '{{}}',
        t1_return FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ
    );
    ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = '{TABLE}'::regclass
              AND conname = '{TABLE}_trade_date_symbol_decision_key'
        ) THEN
            ALTER TABLE {TABLE} DROP CONSTRAINT {TABLE}_trade_date_symbol_decision_key;
        END IF;
    END $$;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_legacy_trade_date_symbol_decision
        ON {TABLE}(trade_date, symbol, decision) WHERE production_run_id IS NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_production_run_symbol_decision
        ON {TABLE}(production_run_id, symbol, decision) WHERE production_run_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_trade_date ON {TABLE}(trade_date);
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_symbol ON {TABLE}(symbol);
    CREATE INDEX IF NOT EXISTS idx_{TABLE}_production_run ON {TABLE}(production_run_id, trade_date);
    """
    try:
        with get_db() as db:
            db.execute(text(ddl))
        mig = migrate_embedding_dimension(target_dim)
        with get_db() as db:
            try:
                db.execute(text(
                    f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_embedding_hnsw '
                    f'ON {TABLE} USING hnsw (embedding vector_cosine_ops)'
                ))
            except Exception:
                pass
        _sync_public_constants()
        return {
            'status': 'OK',
            'table': TABLE,
            'dim': target_dim,
            'embed_method': method,
            'backend': resolve_embed_backend(),
            'migrate': mig,
            'neural_error': _neural_error,
        }
    except Exception as exc:
        return {'status': 'FAILED', 'error': str(exc)}


def upsert_pick_case(
    *,
    trade_date: date | str,
    symbol: str,
    decision: str,
    stock_name: str = '',
    final_score: Optional[float] = None,
    evidence_card: Optional[Dict[str, Any]] = None,
    features: Optional[Dict[str, Any]] = None,
    reason: str = '',
    metadata: Optional[Dict[str, Any]] = None,
    t1_return: Optional[float] = None,
    production_run_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Store or update a pick/top candidate case embedding for later similarity search."""
    # The same case store owns both the official signal and its Top10
    # observation cohort. Both remain read-only evidence for production
    # ranking; TOP10 must not be confused with a second pick path.
    allowed = {'PAPER_PICK', 'TOP10'}
    if dry_run or decision not in allowed:
        return {'status': 'SKIPPED', 'reason': 'dry_run_or_non_pick'}
    ensure = ensure_case_embedding_table()
    if ensure.get('status') not in ('OK',):
        return {'status': 'UNAVAILABLE', 'ensure': ensure}
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': str(exc)}

    symbol = str(symbol or '').zfill(6)
    name = stock_name or str((features or {}).get('name') or (features or {}).get('stock_name') or '')
    case_text = case_text_from_pick(
        symbol=symbol,
        name=name,
        decision=decision,
        score=final_score,
        evidence_card=evidence_card,
        features=features,
        reason=reason,
    )
    numeric = _numeric_features_from_payload(
        score=final_score, features=features, evidence_card=evidence_card,
    )
    emb = embed_text(case_text, numeric=numeric)
    dim = len(emb)
    vec_lit = _vector_literal(emb)
    td = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date)[:10])
    meta = dict(metadata or {})
    meta['production_run_id'] = production_run_id or None
    meta['embed_dim'] = dim
    meta['embed_method'] = embed_method_name()
    meta['embed_backend'] = resolve_embed_backend()
    if resolve_embed_backend() == 'neural':
        meta['embed_model'] = neural_model_name()
    else:
        meta['numeric_slots'] = NUMERIC_SLOTS
    if evidence_card:
        meta['one_liner'] = evidence_card.get('one_liner') or ''
    if reason:
        meta.setdefault('reason', str(reason)[:400])

    conflict_target = (
        "ON CONFLICT (production_run_id, symbol, decision) WHERE production_run_id IS NOT NULL"
        if production_run_id else
        "ON CONFLICT (trade_date, symbol, decision) WHERE production_run_id IS NULL"
    )
    sql = text(f"""
        INSERT INTO {TABLE}
            (trade_date, symbol, decision, production_run_id, stock_name, final_score,
             case_text, embedding, metadata, t1_return, updated_at)
        VALUES
            (:trade_date, :symbol, :decision, :production_run_id, :stock_name, :final_score,
             :case_text, CAST(:embedding AS vector), CAST(:metadata AS jsonb), :t1_return, NOW())
        {conflict_target} DO UPDATE SET
            stock_name = EXCLUDED.stock_name,
            final_score = EXCLUDED.final_score,
            case_text = EXCLUDED.case_text,
            embedding = EXCLUDED.embedding,
            metadata = EXCLUDED.metadata,
            t1_return = COALESCE(EXCLUDED.t1_return, {TABLE}.t1_return),
            updated_at = NOW()
        RETURNING id
    """)
    try:
        with get_db() as db:
            row = db.execute(sql, {
                'trade_date': td,
                'symbol': symbol,
                'decision': decision,
                'production_run_id': production_run_id,
                'stock_name': name[:30],
                'final_score': final_score,
                'case_text': case_text[:8000],
                'embedding': vec_lit,
                'metadata': json.dumps(meta, ensure_ascii=False, default=str),
                't1_return': t1_return,
            }).fetchone()
        return {
            'status': 'OK',
            'id': row[0] if row else None,
            'symbol': symbol,
            'trade_date': td.isoformat(),
            'dim': dim,
            'embed_method': meta['embed_method'],
            'embed_backend': meta['embed_backend'],
        }
    except Exception as exc:
        return {'status': 'FAILED', 'error': str(exc)}


def search_similar_cases(
    *,
    query_text: str = '',
    evidence_card: Optional[Dict[str, Any]] = None,
    features: Optional[Dict[str, Any]] = None,
    symbol: str = '',
    name: str = '',
    score: Any = None,
    exclude_trade_date: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Cosine similarity search over historical pick cases (true pgvector)."""
    if not query_text:
        query_text = case_text_from_pick(
            symbol=symbol,
            name=name,
            decision='QUERY',
            score=score,
            evidence_card=evidence_card,
            features=features,
        )
    if not query_text.strip():
        return []
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception:
        return []
    ensure = ensure_case_embedding_table()
    if ensure.get('status') != 'OK':
        return []
    numeric = _numeric_features_from_payload(
        score=score, features=features, evidence_card=evidence_card,
    )
    emb = embed_text(query_text, numeric=numeric)
    vec_lit = _vector_literal(emb)
    sql = text(f"""
        SELECT trade_date, symbol, decision, stock_name, final_score, t1_return,
               metadata, case_text,
               1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM {TABLE}
        WHERE embedding IS NOT NULL
          AND t1_return IS NOT NULL
          AND (
              :exclude_date IS NULL
              OR trade_date < CAST(:exclude_date AS date)
          )
          AND (:exclude_symbol IS NULL OR symbol <> :exclude_symbol)
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)
    try:
        with get_db() as db:
            rows = db.execute(sql, {
                'embedding': vec_lit,
                'exclude_date': exclude_trade_date,
                'exclude_symbol': str(symbol).zfill(6) if symbol else None,
                'limit': int(limit),
            }).fetchall()
        results = []
        for r in rows:
            meta = r[6] if isinstance(r[6], dict) else {}
            if isinstance(r[6], str):
                try:
                    meta = json.loads(r[6])
                except Exception:
                    meta = {}
            results.append({
                'trade_date': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
                'symbol': r[1],
                'decision': r[2],
                'stock_name': r[3] or '',
                'final_score': r[4],
                't1_return': r[5],
                'similarity': round(float(r[8]), 4) if r[8] is not None else None,
                'one_liner': (meta or {}).get('one_liner') or str(r[7] or '')[:120],
                'embed_method': (meta or {}).get('embed_method'),
                'case_label_status': 'MATURED',
                'matured_at': (meta or {}).get('matured_at'),
            })
        return results
    except Exception:
        return []


def similar_cases_ranking_boost(similar: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bounded soft boost/demotion from similar historical cases (never hard force).

    Wins still lift ranking modestly. Similar-loss neighborhoods demote more
    aggressively (majority-loss + severe avg T1), still soft-only.
    """
    empty = {
        'boost': 0.0,
        'n': 0,
        'n_with_t1': 0,
        'n_loss': 0,
        'loss_ratio': None,
        'avg_t1': None,
        'avg_sim': None,
        'loss_demotion': 0.0,
        'hard_gate': False,
        'force_pick': False,
        'soft_only': True,
    }
    if not similar:
        return empty
    t1s = [float(x['t1_return']) for x in similar if x.get('t1_return') is not None]
    sims = [float(x['similarity']) for x in similar if x.get('similarity') is not None]
    avg_t1 = sum(t1s) / len(t1s) if t1s else None
    avg_sim = sum(sims) / len(sims) if sims else None
    n_loss = sum(1 for t in t1s if t < 0.0)
    loss_ratio = (n_loss / len(t1s)) if t1s else None
    boost = 0.0
    loss_demotion = 0.0
    # Slightly lower sim floor than before so weak-but-real loss neighbors still demote.
    if avg_t1 is not None and avg_sim is not None and avg_sim >= 0.30:
        if avg_t1 >= 0.0:
            boost = min(0.35, avg_t1 * 2.0 * avg_sim)
        else:
            # Asymmetric: similar losers demote harder than winners boost.
            boost = max(-0.45, avg_t1 * 3.0 * avg_sim)
            if loss_ratio is not None and loss_ratio >= 0.60 and avg_sim >= 0.35:
                loss_demotion = min(
                    0.22,
                    0.10 + 0.20 * max(0.0, loss_ratio - 0.60) + max(0.0, -avg_t1) * 0.80,
                )
                boost = max(-0.50, boost - loss_demotion)
    return {
        'boost': round(boost, 4),
        'n': len(similar),
        'n_with_t1': len(t1s),
        'n_loss': n_loss,
        'loss_ratio': round(loss_ratio, 4) if loss_ratio is not None else None,
        'avg_t1': round(avg_t1, 4) if avg_t1 is not None else None,
        'avg_sim': round(avg_sim, 4) if avg_sim is not None else None,
        'loss_demotion': round(loss_demotion, 4),
        'hard_gate': False,
        'force_pick': False,
        'soft_only': True,
    }


def attach_similar_cases_soft_bias(
    row: Dict[str, Any],
    *,
    exclude_trade_date: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Attach similar_cases + soft boost onto a candidate row (in-place). Soft only."""
    if not isinstance(row, dict):
        return {'status': 'SKIPPED', 'reason': 'not_dict'}
    if isinstance(row.get('similar_cases'), list) and row.get('similar_cases') and row.get('similar_cases_boost') is not None:
        meta = row.get('similar_cases_meta') if isinstance(row.get('similar_cases_meta'), dict) else {}
        if not meta:
            meta = similar_cases_ranking_boost(list(row.get('similar_cases') or []))
        if 'case_label_status' not in meta:
            meta['case_label_status'] = 'MATURED' if all(
                str(item.get('case_label_status') or '').upper() == 'MATURED'
                for item in (row.get('similar_cases') or [])
            ) else 'UNKNOWN'
        row['similar_cases_meta'] = meta
        row['similar_cases_boost'] = float(meta.get('boost') or 0.0)
        return {'status': 'CACHED', 'boost': row.get('similar_cases_boost'), 'meta': meta}
    symbol = str(row.get('symbol') or row.get('code') or '')
    name = str(row.get('name') or row.get('stock_name') or '')
    td = exclude_trade_date or str(row.get('trade_date') or row.get('date') or '') or None
    try:
        similar = search_similar_cases(
            symbol=symbol,
            name=name,
            features=row,
            score=row.get('final_score') or row.get('score'),
            exclude_trade_date=td,
            limit=int(limit),
        )
    except Exception as exc:
        return {'status': 'FAILED', 'error': f'{type(exc).__name__}:{exc}'}
    meta = similar_cases_ranking_boost(similar)
    meta['case_label_status'] = 'MATURED' if similar and all(
        str(item.get('case_label_status') or '').upper() == 'MATURED'
        for item in similar
    ) else 'UNKNOWN'
    row['similar_cases'] = similar
    row['similar_cases_meta'] = meta
    row['similar_cases_boost'] = float(meta.get('boost') or 0.0)
    return {'status': 'OK', 'boost': row['similar_cases_boost'], 'n': len(similar), 'meta': meta}


def rebuild_all_case_embeddings(*, limit: Optional[int] = None) -> Dict[str, Any]:
    """Re-embed all stored cases with current backend (migrates dim if needed)."""
    ensure = ensure_case_embedding_table()
    if ensure.get('status') != 'OK':
        return {'status': 'UNAVAILABLE', 'ensure': ensure}
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': str(exc)}

    target_dim = get_embed_dim()
    method = embed_method_name()
    backend = resolve_embed_backend()
    lim = f'LIMIT {int(limit)}' if limit else ''
    stats: Dict[str, Any] = {
        'status': 'OK',
        'updated': 0,
        'failed': 0,
        'embed_method': method,
        'embed_backend': backend,
        'dim': target_dim,
        'model': neural_model_name() if backend == 'neural' else None,
        'migrate': ensure.get('migrate'),
    }
    with get_db() as db:
        rows = db.execute(text(f"""
            SELECT id, trade_date, symbol, decision, stock_name, final_score,
                   case_text, metadata, t1_return
            FROM {TABLE}
            ORDER BY trade_date DESC, id DESC
            {lim}
        """)).fetchall()
        for r in rows:
            try:
                meta = r[7] if isinstance(r[7], dict) else {}
                if isinstance(r[7], str):
                    try:
                        meta = json.loads(r[7])
                    except Exception:
                        meta = {}
                meta = dict(meta or {})
                case_text = r[6] or ''
                # Prefer re-building text with numeric crumbs when case_text is thin.
                if len(case_text) < 20:
                    case_text = case_text_from_pick(
                        symbol=str(r[2] or ''),
                        name=str(r[4] or ''),
                        decision=str(r[3] or ''),
                        score=r[5],
                        features={
                            'final_score': r[5],
                            't1_return': r[8],
                            'rank': meta.get('rank'),
                        },
                        reason=str(meta.get('reason') or ''),
                    )
                numeric = _numeric_features_from_payload(
                    score=r[5],
                    features={
                        'final_score': r[5],
                        't1_return': r[8],
                        'rank': meta.get('rank'),
                        'fund_flow_momentum': meta.get('fund_flow_momentum'),
                        'main_theme_core_score': meta.get('main_theme_core_score'),
                        'sector_opportunity_score': meta.get('sector_opportunity_score'),
                        'signal_pct': meta.get('signal_pct'),
                    },
                )
                emb = embed_text(case_text, numeric=numeric)
                meta['embed_method'] = method
                meta['embed_backend'] = backend
                meta['embed_dim'] = len(emb)
                if backend == 'neural':
                    meta['embed_model'] = neural_model_name()
                meta['rebuilt_at'] = datetime.now(timezone.utc).isoformat()
                db.execute(text(f"""
                    UPDATE {TABLE}
                    SET embedding = CAST(:embedding AS vector),
                        case_text = COALESCE(NULLIF(:case_text, ''), case_text),
                        metadata = CAST(:metadata AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                """), {
                    'id': r[0],
                    'embedding': _vector_literal(emb),
                    'case_text': case_text[:8000],
                    'metadata': json.dumps(meta, ensure_ascii=False, default=str),
                })
                stats['updated'] += 1
            except Exception as exc:
                stats['failed'] += 1
                stats.setdefault('errors', []).append(str(exc)[:160])
    _sync_public_constants()
    return stats


def upsert_top10_cases_from_db(
    trade_date: date | str,
    production_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist top10 daily_candidates as TOP10 vectors + attach t1_return when present."""
    ensure = ensure_case_embedding_table()
    if ensure.get('status') != 'OK':
        return {'status': 'UNAVAILABLE', 'ensure': ensure}
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': str(exc)}
    td = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date)[:10])
    out: Dict[str, Any] = {
        'status': 'OK',
        'trade_date': td.isoformat(),
        'production_run_id': production_run_id or None,
        'upserted': 0,
        'failed': 0,
        'items': [],
        'embed_method': embed_method_name(),
        'embed_backend': resolve_embed_backend(),
    }
    run_clause = (
        'AND dc.production_run_id = :production_run_id'
        if production_run_id
        else 'AND dc.production_run_id IS NULL'
    )
    return_join = (
        'r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol'
        if production_run_id
        else 'r.trade_date = dc.trade_date AND r.symbol = dc.symbol AND r.production_run_id IS NULL'
    )
    with get_db() as db:
        rows = db.execute(text(f"""
            SELECT dc.symbol, dc.stock_name, dc.rank, dc.final_score, dc.decision,
                   dc.selection_outcome, dc.selection_reason, dc.ticket_reason,
                   dc.not_selected_reason, dc.auxiliary_evidence_snapshot,
                   dc.ranking_basis, dc.is_official_pick,
                   r.t1_return
            FROM daily_candidates dc
            LEFT JOIN returns r
              ON {return_join}
            WHERE dc.trade_date = :td AND dc.rank IS NOT NULL AND dc.rank <= 10
              """ + run_clause + """
            ORDER BY dc.rank
        """), {'td': td, 'production_run_id': production_run_id}).mappings().all()
    for row in rows:
        d = dict(row)
        reason_bits = [
            f"rank={d.get('rank')}",
            str(d.get('selection_outcome') or ''),
            str(d.get('selection_reason') or '')[:200],
            str(d.get('ticket_reason') or '')[:200],
            str(d.get('not_selected_reason') or '')[:200],
        ]
        reason = ' | '.join(x for x in reason_bits if x and x != 'None')
        features = {
            'rank': d.get('rank'),
            'final_score': d.get('final_score'),
            'selection_outcome': d.get('selection_outcome'),
            'ticket_reason': d.get('ticket_reason'),
            'not_selected_reason': d.get('not_selected_reason'),
            'is_official_pick': d.get('is_official_pick'),
            'auxiliary_evidence_snapshot': d.get('auxiliary_evidence_snapshot'),
            'ranking_basis': d.get('ranking_basis'),
            't1_return': d.get('t1_return'),
        }
        res = upsert_pick_case(
            trade_date=td,
            symbol=str(d.get('symbol') or ''),
            decision='TOP10',
            stock_name=str(d.get('stock_name') or ''),
            final_score=float(d['final_score']) if d.get('final_score') is not None else None,
            features=features,
            reason=reason,
            metadata={
                'cohort': 'top10',
                'production_run_id': production_run_id or None,
                'rank': d.get('rank'),
                'selection_outcome': d.get('selection_outcome'),
                't1_return_high': d.get('t1_return_high'),
            },
            t1_return=(
                float(d['t1_return']) if d.get('t1_return') is not None else None
            ),
            production_run_id=production_run_id,
        )
        if res.get('status') == 'OK':
            out['upserted'] += 1
        else:
            out['failed'] += 1
        out['items'].append({'symbol': d.get('symbol'), 'rank': d.get('rank'), 'upsert': res})
    return out


def backend_status() -> Dict[str, Any]:
    """Diagnostic snapshot for ops / knowledge export."""
    req = _requested_backend()
    try:
        backend = resolve_embed_backend()
        dim = get_embed_dim()
        method = embed_method_name()
        ok = True
        err = _neural_error
    except Exception as exc:
        backend = 'unavailable'
        dim = None
        method = None
        ok = False
        err = str(exc)
    return {
        'requested': req,
        'resolved': backend,
        'ok': ok,
        'dim': dim,
        'embed_method': method,
        'model': neural_model_name() if backend == 'neural' else None,
        'neural_error': err,
        'sentence_transformers': neural_available() if req != 'structured' else None,
    }
