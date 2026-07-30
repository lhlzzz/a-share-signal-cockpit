from datetime import date

import scripts.xiaogu_knowledge_asset_export as export


def test_export_historical_knowledge_aggregates_cases(monkeypatch):
    dates = [date(2026, 7, 28), date(2026, 7, 29)]
    monkeypatch.setattr(export, 'historical_knowledge_dates', lambda *_args: dates)
    monkeypatch.setattr(
        export,
        'upsert_top10_cases_from_db',
        lambda td: {'upserted': 10 if td.day == 28 else 8, 'failed': 0},
    )
    monkeypatch.setattr(
        export,
        'upsert_paper_pick_cases_from_db',
        lambda td: {'upserted': 1, 'failed': 0},
    )
    monkeypatch.setattr(
        export,
        'rebuild_all_case_embeddings',
        lambda: {'status': 'OK', 'updated': 20, 'failed': 0},
    )

    report = export.export_historical_knowledge(
        date(2026, 7, 28),
        date(2026, 7, 29),
        rebuild_vectors=True,
    )

    assert report['dates'] == 2
    assert report['top10_upserted'] == 18
    assert report['paper_pick_upserted'] == 2
    assert report['failed'] == 0
    assert report['rebuild_vectors']['updated'] == 20
    assert [row['trade_date'] for row in report['date_results']] == [
        '2026-07-28',
        '2026-07-29',
    ]
