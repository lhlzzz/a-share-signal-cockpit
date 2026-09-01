"""Immutable snapshot identity, hash, and concurrency regression lock."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from xiaogu_forward_snapshot import snapshot_payload_hash, validate_and_build_canonical_snapshot


def _lock_snapshot(
    *,
    symbol: str,
    lineage_id: str,
    price: float = 10.0,
    source_time: str = "2026-08-26T14:50:00+08:00",
    extra: dict | None = None,
):
    payload = {
        "symbol": symbol,
        "price": price,
        "open": 9.9,
        "high": 10.3,
        "low": 9.7,
        "amount": 1000,
        "volume": 100,
        "trade_date": "2026-08-26",
        "source_time": source_time,
        "lineage_id": lineage_id,
    }
    if extra:
        payload.update(extra)
    return validate_and_build_canonical_snapshot(payload, lineage_id=lineage_id)


def _cleanup(snapshot_ids: list[str]) -> None:
    from xiaogu_db import engine

    if not snapshot_ids:
        return
    with engine.begin() as db:
        for snapshot_id in snapshot_ids:
            db.execute(
                text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :sid"),
                {"sid": snapshot_id},
            )
            db.execute(
                text("DELETE FROM snapshots WHERE snapshot_id = :sid"),
                {"sid": snapshot_id},
            )


def test_same_payload_same_hash():
    left = {"symbol": "600001", "trade_date": "2026-08-26", "price": 10, "source_time": "2026-08-26T14:50:00+08:00"}
    right = {"symbol": "600001", "trade_date": "2026-08-26", "price": 10, "source_time": "2026-08-26T14:50:00+08:00"}
    assert snapshot_payload_hash(left) == snapshot_payload_hash(right)


def test_key_order_does_not_change_hash():
    left = {"b": 2, "a": 1, "source_time": "2026-08-26T14:50:00+08:00"}
    right = {"a": 1, "source_time": "2026-08-26T14:50:00+08:00", "b": 2}
    assert snapshot_payload_hash(left) == snapshot_payload_hash(right)


def test_timezone_normalization():
    base = {"symbol": "600001", "trade_date": "2026-08-26", "price": 10}
    plus_eight = {**base, "source_time": "2026-08-26T14:50:00+08:00"}
    utc = {**base, "source_time": "2026-08-26T06:50:00+00:00"}
    zulu = {**base, "source_time": "2026-08-26T06:50:00Z"}
    dt = {**base, "source_time": datetime(2026, 8, 26, 14, 50, tzinfo=ZoneInfo("Asia/Shanghai"))}
    utc_dt = {**base, "source_time": datetime(2026, 8, 26, 6, 50, tzinfo=timezone.utc)}
    assert snapshot_payload_hash(plus_eight) == snapshot_payload_hash(utc)
    assert snapshot_payload_hash(plus_eight) == snapshot_payload_hash(zulu)
    assert snapshot_payload_hash(plus_eight) == snapshot_payload_hash(dt)
    assert snapshot_payload_hash(plus_eight) == snapshot_payload_hash(utc_dt)


def test_number_normalization():
    left = {"symbol": "600001", "price": 10, "volume": 1, "trade_date": "2026-08-26"}
    right = {"symbol": "600001", "price": 10.0, "volume": 1.0, "trade_date": "2026-08-26"}
    assert snapshot_payload_hash(left) == snapshot_payload_hash(right)


def test_different_payload_different_hash():
    left = {"symbol": "600001", "price": 10, "trade_date": "2026-08-26"}
    right = {"symbol": "600001", "price": 11, "trade_date": "2026-08-26"}
    assert snapshot_payload_hash(left) != snapshot_payload_hash(right)


def test_snapshot_hash_stable():
    test_same_payload_same_hash()
    test_key_order_does_not_change_hash()


def test_snapshot_hash_changes_on_fact_change():
    test_different_payload_different_hash()


def test_snapshot_insert():
    from xiaogu_db import SNAPSHOT_INSERTED, ensure_production_schema, record_snapshot

    snapshot = _lock_snapshot(symbol="609001", lineage_id="lock-insert-lineage")
    ensure_production_schema()
    _cleanup([snapshot["snapshot_id"]])
    try:
        assert record_snapshot(snapshot) == SNAPSHOT_INSERTED
    finally:
        _cleanup([snapshot["snapshot_id"]])


def test_snapshot_same_hash_idempotent():
    from xiaogu_db import SNAPSHOT_IDEMPOTENT, SNAPSHOT_INSERTED, engine, ensure_production_schema, record_snapshot

    snapshot = _lock_snapshot(symbol="609002", lineage_id="lock-idempotent-lineage")
    ensure_production_schema()
    _cleanup([snapshot["snapshot_id"]])
    try:
        assert record_snapshot(snapshot) == SNAPSHOT_INSERTED
        assert record_snapshot(dict(snapshot)) == SNAPSHOT_IDEMPOTENT
        with engine.connect() as db:
            count = db.execute(
                text("SELECT count(*) FROM snapshots WHERE snapshot_id = :sid"),
                {"sid": snapshot["snapshot_id"]},
            ).scalar()
        assert count == 1
    finally:
        _cleanup([snapshot["snapshot_id"]])


def test_snapshot_different_hash_conflict():
    from xiaogu_db import (
        SNAPSHOT_IDENTITY_CONFLICT,
        SNAPSHOT_INSERTED,
        engine,
        ensure_production_schema,
        find_snapshot_identity_conflicts,
        record_snapshot,
    )

    first = _lock_snapshot(symbol="609003", lineage_id="lock-conflict-lineage", price=10)
    second = _lock_snapshot(symbol="609003", lineage_id="lock-conflict-lineage", price=99)
    second["snapshot_id"] = first["snapshot_id"]
    second["payload_hash"] = snapshot_payload_hash(second)
    ensure_production_schema()
    _cleanup([first["snapshot_id"]])
    try:
        assert record_snapshot(first) == SNAPSHOT_INSERTED
        with pytest.raises(ValueError, match=SNAPSHOT_IDENTITY_CONFLICT):
            record_snapshot(second)
        with engine.connect() as db:
            row = db.execute(
                text("SELECT payload_hash FROM snapshots WHERE snapshot_id = :sid"),
                {"sid": first["snapshot_id"]},
            ).mappings().first()
            count = db.execute(
                text("SELECT count(*) FROM snapshots WHERE snapshot_id = :sid"),
                {"sid": first["snapshot_id"]},
            ).scalar()
        assert row["payload_hash"] == first["payload_hash"]
        assert count == 1
        conflicts = [
            item for item in find_snapshot_identity_conflicts() if item["snapshot_id"] == first["snapshot_id"]
        ]
        assert conflicts
        assert conflicts[0]["existing_hash"] == first["payload_hash"]
        assert conflicts[0]["incoming_hash"] == second["payload_hash"]
    finally:
        _cleanup([first["snapshot_id"]])


def test_snapshot_concurrent_same_hash():
    test_snapshot_concurrent_same_hash_is_idempotent()


def test_snapshot_concurrent_same_hash_is_idempotent():
    from xiaogu_db import SNAPSHOT_IDEMPOTENT, SNAPSHOT_INSERTED, engine, ensure_production_schema, record_snapshot

    snapshot = _lock_snapshot(symbol="609004", lineage_id="lock-conc-same-lineage")
    ensure_production_schema()
    _cleanup([snapshot["snapshot_id"]])

    def write():
        return record_snapshot(dict(snapshot))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in as_completed([pool.submit(write), pool.submit(write)])]
        assert set(results) <= {SNAPSHOT_INSERTED, SNAPSHOT_IDEMPOTENT}
        assert SNAPSHOT_INSERTED in results or results == [SNAPSHOT_IDEMPOTENT, SNAPSHOT_IDEMPOTENT]
        with engine.connect() as db:
            count = db.execute(
                text("SELECT count(*) FROM snapshots WHERE snapshot_id = :sid"),
                {"sid": snapshot["snapshot_id"]},
            ).scalar()
            hashes = list(
                db.execute(
                    text("SELECT payload_hash FROM snapshots WHERE snapshot_id = :sid"),
                    {"sid": snapshot["snapshot_id"]},
                ).scalars()
            )
        assert count == 1
        assert hashes == [snapshot["payload_hash"]]
    finally:
        _cleanup([snapshot["snapshot_id"]])


def test_snapshot_concurrent_different_hash():
    test_snapshot_concurrent_different_hash_conflicts()


def test_snapshot_concurrent_different_hash_conflicts():
    from xiaogu_db import (
        SNAPSHOT_IDENTITY_CONFLICT,
        SNAPSHOT_INSERTED,
        engine,
        ensure_production_schema,
        record_snapshot,
    )

    first = _lock_snapshot(symbol="609005", lineage_id="lock-conc-diff-lineage", price=10)
    second = _lock_snapshot(symbol="609005", lineage_id="lock-conc-diff-lineage", price=77)
    second["snapshot_id"] = first["snapshot_id"]
    second["payload_hash"] = snapshot_payload_hash(second)
    ensure_production_schema()
    _cleanup([first["snapshot_id"]])

    def write(snapshot):
        try:
            return record_snapshot(snapshot)
        except ValueError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                future.result()
                for future in as_completed([pool.submit(write, dict(first)), pool.submit(write, dict(second))])
            ]
        assert SNAPSHOT_INSERTED in results
        assert SNAPSHOT_IDENTITY_CONFLICT in results
        with engine.connect() as db:
            rows = list(
                db.execute(
                    text("SELECT snapshot_id, payload_hash FROM snapshots WHERE snapshot_id = :sid"),
                    {"sid": first["snapshot_id"]},
                ).mappings()
            )
        assert len(rows) == 1
        assert rows[0]["payload_hash"] in {first["payload_hash"], second["payload_hash"]}
    finally:
        _cleanup([first["snapshot_id"]])


def test_snapshot_row_immutable():
    from xiaogu_db import SNAPSHOT_INSERTED, engine, ensure_production_schema, record_snapshot

    snapshot = _lock_snapshot(symbol="609006", lineage_id="lock-immutable-lineage")
    ensure_production_schema()
    _cleanup([snapshot["snapshot_id"]])
    try:
        assert record_snapshot(snapshot) == SNAPSHOT_INSERTED
        with pytest.raises(Exception, match="SNAPSHOT_IDENTITY_IMMUTABLE"):
            with engine.begin() as db:
                db.execute(
                    text("UPDATE snapshots SET payload = CAST(:payload AS jsonb) WHERE snapshot_id = :sid"),
                    {"payload": '{"mutated": true}', "sid": snapshot["snapshot_id"]},
                )
        with pytest.raises(Exception, match="SNAPSHOT_IDENTITY_IMMUTABLE"):
            with engine.begin() as db:
                db.execute(
                    text("UPDATE snapshots SET payload_hash = :hash WHERE snapshot_id = :sid"),
                    {"hash": "deadbeef", "sid": snapshot["snapshot_id"]},
                )
    finally:
        _cleanup([snapshot["snapshot_id"]])


def test_snapshot_persisted_verification():
    from xiaogu_db import SNAPSHOT_INSERTED, ensure_production_schema, record_snapshot, verify_persisted_snapshot

    snapshot = _lock_snapshot(symbol="609007", lineage_id="lock-verify-lineage")
    ensure_production_schema()
    _cleanup([snapshot["snapshot_id"]])
    try:
        assert record_snapshot(snapshot) == SNAPSHOT_INSERTED
        assert verify_persisted_snapshot(
            snapshot_id=snapshot["snapshot_id"],
            payload_hash=snapshot["payload_hash"],
            symbol=snapshot["symbol"],
            trade_date=snapshot["trade_date"],
        )
        assert not verify_persisted_snapshot(
            snapshot_id=snapshot["snapshot_id"],
            payload_hash="not-the-hash",
            symbol=snapshot["symbol"],
            trade_date=snapshot["trade_date"],
        )
    finally:
        _cleanup([snapshot["snapshot_id"]])


def test_snapshot_identity_conflict_on_different_lineage_payload():
    from xiaogu_db import SNAPSHOT_IDENTITY_CONFLICT, SNAPSHOT_INSERTED, ensure_production_schema, record_snapshot

    first = _lock_snapshot(symbol="609008", lineage_id="lock-lineage-a", price=10)
    second = _lock_snapshot(symbol="609008", lineage_id="lock-lineage-b", price=11)
    second["snapshot_id"] = first["snapshot_id"]
    second["payload_hash"] = snapshot_payload_hash(second)
    ensure_production_schema()
    _cleanup([first["snapshot_id"]])
    try:
        assert record_snapshot(first) == SNAPSHOT_INSERTED
        with pytest.raises(ValueError, match=SNAPSHOT_IDENTITY_CONFLICT):
            record_snapshot(second)
    finally:
        _cleanup([first["snapshot_id"]])


def _historical_lock_snapshot(*, snapshot_id: str, lineage_id: str, symbol: str, price: float = 10.0):
    return {
        "snapshot_id": snapshot_id,
        "lineage_id": lineage_id,
        "symbol": symbol,
        "trade_date": "2026-08-26",
        "signal_time": "2026-08-26T14:50:00+08:00",
        "source": "test_hist_lock",
        "source_timestamp": "2026-08-26T14:50:00+08:00",
        "snapshot_version": "canonical_snapshot_v2",
        "point_in_time": True,
        "available_at": "2026-08-26T14:50:00+08:00",
        "price_basis": "UNADJUSTED",
        "price": price,
    }


def _cleanup_historical(snapshot_ids: list[str]) -> None:
    from xiaogu_db import engine

    if not snapshot_ids:
        return
    with engine.begin() as db:
        for snapshot_id in snapshot_ids:
            db.execute(
                text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :sid"),
                {"sid": snapshot_id},
            )
            db.execute(
                text("DELETE FROM canonical_historical_snapshots WHERE snapshot_id = :sid"),
                {"sid": snapshot_id},
            )


def test_historical_snapshot_same_hash_idempotent():
    from xiaogu_db import engine, ensure_production_schema, record_canonical_historical_snapshots

    snapshot = _historical_lock_snapshot(
        snapshot_id="hist-lock-idempotent-id",
        lineage_id="hist-lock-idempotent-lineage",
        symbol="609101",
    )
    ensure_production_schema()
    _cleanup_historical([snapshot["snapshot_id"]])
    try:
        record_canonical_historical_snapshots([snapshot])
        record_canonical_historical_snapshots([dict(snapshot)])
        with engine.connect() as db:
            count = db.execute(
                text("SELECT count(*) FROM canonical_historical_snapshots WHERE snapshot_id = :sid"),
                {"sid": snapshot["snapshot_id"]},
            ).scalar()
        assert count == 1
    finally:
        _cleanup_historical([snapshot["snapshot_id"]])


def test_historical_snapshot_different_hash_conflict():
    from xiaogu_db import (
        SNAPSHOT_IDENTITY_CONFLICT,
        engine,
        ensure_production_schema,
        record_canonical_historical_snapshots,
    )

    first = _historical_lock_snapshot(
        snapshot_id="hist-lock-conflict-id",
        lineage_id="hist-lock-conflict-lineage",
        symbol="609102",
        price=10,
    )
    second = _historical_lock_snapshot(
        snapshot_id="hist-lock-conflict-id",
        lineage_id="hist-lock-conflict-lineage",
        symbol="609102",
        price=99,
    )
    ensure_production_schema()
    _cleanup_historical([first["snapshot_id"]])
    try:
        record_canonical_historical_snapshots([first])
        with pytest.raises(ValueError, match=SNAPSHOT_IDENTITY_CONFLICT):
            record_canonical_historical_snapshots([second])
        with engine.connect() as db:
            count = db.execute(
                text("SELECT count(*) FROM canonical_historical_snapshots WHERE snapshot_id = :sid"),
                {"sid": first["snapshot_id"]},
            ).scalar()
            payload = db.execute(
                text("SELECT payload FROM canonical_historical_snapshots WHERE snapshot_id = :sid"),
                {"sid": first["snapshot_id"]},
            ).scalar()
        assert count == 1
        assert payload["price"] == 10
    finally:
        _cleanup_historical([first["snapshot_id"]])
