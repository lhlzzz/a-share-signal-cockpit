"""DB-First 复盘报表：即时收益、滞后收益、rank bucket、setup_type"""
import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xiaogu_db import engine
from xiaogu_backtest_v0_1 import build_db_cohort_report
from sqlalchemy import text


def _query(sql, params=None):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]


def report_rank_bucket():
    """按rank bucket统计收益"""
    rows = _query("""
        SELECT
            CASE WHEN d.rank BETWEEN 1 AND 3 THEN 'Rank 1-3'
                 WHEN d.rank BETWEEN 4 AND 6 THEN 'Rank 4-6'
                 WHEN d.rank BETWEEN 7 AND 10 THEN 'Rank 7-10'
                 ELSE 'Rank 11+' END as bucket,
            COUNT(*) as n,
            ROUND(AVG(r.t1_return)::numeric, 4) as avg_t1,
            ROUND(AVG(r.t2_return)::numeric, 4) as avg_t2,
            ROUND(AVG(r.t3_return)::numeric, 4) as avg_t3,
            ROUND(COUNT(CASE WHEN r.t1_return > 0 THEN 1 END)::numeric / NULLIF(COUNT(CASE WHEN r.t1_return IS NOT NULL THEN 1 END), 0), 3) as win_rate_t1
        FROM daily_candidates d
        LEFT JOIN returns r ON d.trade_date = r.trade_date AND d.symbol = r.symbol
        WHERE d.rank IS NOT NULL AND d.rank <= 10
        GROUP BY bucket
        ORDER BY bucket
    """)
    return rows


def report_setup_type():
    """按setup_type统计收益"""
    rows = _query("""
        SELECT
            COALESCE(d.raw_json->>'setup_type', 'UNKNOWN') as setup_type,
            COUNT(*) as n,
            ROUND(AVG(r.t1_return)::numeric, 4) as avg_t1,
            ROUND(AVG(r.t2_return)::numeric, 4) as avg_t2,
            ROUND(AVG(r.t3_return)::numeric, 4) as avg_t3,
            ROUND(COUNT(CASE WHEN r.t1_return > 0 THEN 1 END)::numeric / NULLIF(COUNT(CASE WHEN r.t1_return IS NOT NULL THEN 1 END), 0), 3) as win_rate_t1
        FROM daily_candidates d
        LEFT JOIN returns r ON d.trade_date = r.trade_date AND d.symbol = r.symbol
        WHERE d.rank IS NOT NULL AND d.rank <= 10
          AND d.raw_json->>'setup_type' IS NOT NULL
        GROUP BY setup_type
        HAVING COUNT(*) >= 2
        ORDER BY avg_t1 DESC NULLS LAST
    """)
    return rows


def report_decision_quality():
    """决策质量对比"""
    rows = _query("""
        WITH official_decisions AS (
            SELECT
                p.trade_date,
                p.symbol,
                CASE
                    WHEN p.decision = 'PAPER_PICK'
                         AND (
                             COALESCE(p.features->>'decision_reason', '') ILIKE '%FALLBACK%'
                             OR COALESCE(p.features::text, '') ILIKE '%FALLBACK%'
                             OR COALESCE(p.blockers::text, '') ILIKE '%DEGRADED%'
                             OR COALESCE(p.features::text, '') ILIKE '%DEGRADED%'
                         )
                        THEN 'PAPER_PICK_FALLBACK'
                    WHEN p.decision = 'PAPER_PICK'
                         AND COALESCE(p.features->>'decision_reason', '') = 'ALL_FORWARD_PAPER_HARD_GATES_PASS'
                        THEN 'PAPER_PICK_CLEAN'
                    WHEN p.decision = 'PAPER_PICK'
                        THEN 'PAPER_PICK_UNCLASSIFIED'
                    ELSE p.decision
                END AS decision_quality
            FROM picks p
            WHERE p.decision IN ('PAPER_PICK', 'NO_PICK', 'RESEARCH_CANDIDATE')
        ),
        candidate_decisions AS (
            SELECT
                d.trade_date,
                d.symbol,
                d.decision AS decision_quality
            FROM daily_candidates d
            WHERE d.decision = 'CANDIDATE'
        ),
        labeled AS (
            SELECT * FROM official_decisions
            UNION ALL
            SELECT * FROM candidate_decisions
        )
        SELECT
            d.decision_quality AS decision,
            COUNT(*) as n,
            ROUND(AVG(r.t1_return)::numeric, 4) as avg_t1,
            ROUND(AVG(r.t2_return)::numeric, 4) as avg_t2,
            ROUND(AVG(r.t3_return)::numeric, 4) as avg_t3,
            ROUND(COUNT(CASE WHEN r.t1_return > 0 THEN 1 END)::numeric / NULLIF(COUNT(CASE WHEN r.t1_return IS NOT NULL THEN 1 END), 0), 3) as win_rate_t1
        FROM labeled d
        LEFT JOIN returns r ON d.trade_date = r.trade_date AND d.symbol = r.symbol
        GROUP BY d.decision_quality
        ORDER BY d.decision_quality
    """)
    return rows


def report_data_completeness():
    """数据完整性"""
    dc = _query("""
        SELECT
            COUNT(DISTINCT trade_date) as dates,
            COUNT(*) as total,
            COUNT(CASE WHEN rank IS NOT NULL AND rank <= 10 THEN 1 END) as top10,
            COUNT(CASE WHEN final_score IS NOT NULL THEN 1 END) as with_score
        FROM daily_candidates
    """)[0]
    ret = _query("""
        SELECT
            COUNT(DISTINCT trade_date) as dates,
            COUNT(*) as total,
            COUNT(CASE WHEN t1_return IS NOT NULL THEN 1 END) as with_t1,
            COUNT(CASE WHEN t2_return IS NOT NULL THEN 1 END) as with_t2,
            COUNT(CASE WHEN t3_return IS NOT NULL THEN 1 END) as with_t3
        FROM returns
    """)[0]
    return {'daily_candidates': dc, 'returns': ret}


def report_late_june_quality(start_date='2026-06-20', end_date=None):
    """Expose the cohort/reconstruction report in the existing review system."""
    report = build_db_cohort_report(start_date, end_date)
    return {
        'late_june_sample_inventory': report.get('inventory', {}),
        'cohort_quality_summary': report.get('cohort_summary', {}),
        'evidence_reconstruction_summary': report.get('reconstruction_summary', {}),
        'mainboard_only_performance': report.get('mainboard_only_since_2026_06_20', {}),
        'legacy_non_mainboard_reference': report.get('legacy_non_mainboard_reference', {}),
        'paper_pick_vs_top10_gap': {
            key: report.get(key, {}).get('paper_pick_vs_top10_best_gap')
            for key in ('all_since_2026_06_20', 'mainboard_only_since_2026_06_20', 'TRANSITION_RECONSTRUCTABLE', 'FULL_CHAIN_COMPLETE')
        },
        'giant_network_case_study': (report.get('case_studies') or {}).get('giant_network', {}),
        'huatian_tech_case_study': (report.get('case_studies') or {}).get('huatian_tech', {}),
    }


def report_daily_top10():
    """每日top10候选明细"""
    rows = _query("""
        SELECT
            d.trade_date, d.symbol, d.stock_name, d.rank, d.final_score,
            d.decision, d.raw_json->>'setup_type' as setup_type,
            r.t1_return, r.t2_return, r.t3_return
        FROM daily_candidates d
        LEFT JOIN returns r ON d.trade_date = r.trade_date AND d.symbol = r.symbol
        WHERE d.rank IS NOT NULL AND d.rank <= 10
        ORDER BY d.trade_date, d.rank
    """)
    return rows


def print_terminal_report():
    """输出终端报表"""
    print("=" * 70)
    print("  xiaogu DB-First 复盘报表")
    print("=" * 70)

    comp = report_data_completeness()
    dc = comp['daily_candidates']
    ret = comp['returns']
    print(f"\n【数据完整性】")
    print(f"  daily_candidates: {dc['dates']}天, {dc['total']}条, {dc['top10']}条top10, {dc['with_score']}条有分数")
    print(f"  returns: {ret['dates']}天, {ret['total']}条, t1={ret['with_t1']}, t2={ret['with_t2']}, t3={ret['with_t3']}")

    print(f"\n【Rank Bucket收益】")
    for r in report_rank_bucket():
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else 'N/A'
        t2 = f"{r['avg_t2']*100:+.2f}%" if r['avg_t2'] is not None else 'N/A'
        t3 = f"{r['avg_t3']*100:+.2f}%" if r['avg_t3'] is not None else 'N/A'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else 'N/A'
        print(f"  {r['bucket']:12s}  t1={t1:>8s}  t2={t2:>8s}  t3={t3:>8s}  win={wr:>4s}  (n={r['n']})")

    print(f"\n【Setup Type收益】")
    for r in report_setup_type():
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else 'N/A'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else 'N/A'
        print(f"  {r['setup_type']:35s}  t1={t1:>8s}  win={wr:>4s}  (n={r['n']})")

    print(f"\n【决策质量】")
    for r in report_decision_quality():
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else 'N/A'
        t2 = f"{r['avg_t2']*100:+.2f}%" if r['avg_t2'] is not None else 'N/A'
        t3 = f"{r['avg_t3']*100:+.2f}%" if r['avg_t3'] is not None else 'N/A'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else 'N/A'
        print(f"  {r['decision']:18s}  t1={t1:>8s}  t2={t2:>8s}  t3={t3:>8s}  win={wr:>4s}  (n={r['n']})")

    print()
    late_june = report_late_june_quality()
    print('【晚六月以来样本分层】')
    print(json.dumps(late_june, ensure_ascii=False, indent=2, default=str))


def generate_html_report(output_path=None):
    """生成HTML报告"""
    if output_path is None:
        output_path = ROOT / 'summary' / 'db_review_report.html'

    comp = report_data_completeness()
    rank_data = report_rank_bucket()
    setup_data = report_setup_type()
    decision_data = report_decision_quality()
    daily_data = report_daily_top10()

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>xiaogu DB-First 复盘报表</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #f5f5f5; }}
h1 {{ color: #333; }}
.card {{ background: white; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
th {{ background: #f0f0f0; }}
td:first-child {{ text-align: left; }}
.pos {{ color: #d32f2f; }}
.neg {{ color: #388e3c; }}
.summary {{ font-size: 18px; font-weight: bold; }}
</style></head><body>
<h1>xiaogu DB-First 复盘报表</h1>
<p class="summary">数据范围: {comp['daily_candidates']['dates']}天 | {comp['daily_candidates']['total']}条候选 | {comp['returns']['total']}条收益</p>
"""

    html += '<div class="card"><h2>Rank Bucket收益</h2><table><tr><th>Bucket</th><th>N</th><th>T+1</th><th>T+2</th><th>T+3</th><th>Win Rate</th></tr>'
    for r in rank_data:
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else '-'
        t2 = f"{r['avg_t2']*100:+.2f}%" if r['avg_t2'] is not None else '-'
        t3 = f"{r['avg_t3']*100:+.2f}%" if r['avg_t3'] is not None else '-'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else '-'
        cls = 'pos' if r['avg_t1'] and r['avg_t1'] > 0 else 'neg' if r['avg_t1'] and r['avg_t1'] < 0 else ''
        html += f'<tr><td>{r["bucket"]}</td><td>{r["n"]}</td><td class="{cls}">{t1}</td><td>{t2}</td><td>{t3}</td><td>{wr}</td></tr>'
    html += '</table></div>'

    html += '<div class="card"><h2>Setup Type收益</h2><table><tr><th>Type</th><th>N</th><th>T+1</th><th>T+2</th><th>T+3</th><th>Win Rate</th></tr>'
    for r in setup_data:
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else '-'
        t2 = f"{r['avg_t2']*100:+.2f}%" if r['avg_t2'] is not None else '-'
        t3 = f"{r['avg_t3']*100:+.2f}%" if r['avg_t3'] is not None else '-'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else '-'
        html += f'<tr><td>{r["setup_type"]}</td><td>{r["n"]}</td><td>{t1}</td><td>{t2}</td><td>{t3}</td><td>{wr}</td></tr>'
    html += '</table></div>'

    html += '<div class="card"><h2>决策质量</h2><table><tr><th>Decision</th><th>N</th><th>T+1</th><th>T+2</th><th>T+3</th><th>Win Rate</th></tr>'
    for r in decision_data:
        t1 = f"{r['avg_t1']*100:+.2f}%" if r['avg_t1'] is not None else '-'
        t2 = f"{r['avg_t2']*100:+.2f}%" if r['avg_t2'] is not None else '-'
        t3 = f"{r['avg_t3']*100:+.2f}%" if r['avg_t3'] is not None else '-'
        wr = f"{r['win_rate_t1']*100:.0f}%" if r['win_rate_t1'] is not None else '-'
        html += f'<tr><td>{r["decision"]}</td><td>{r["n"]}</td><td>{t1}</td><td>{t2}</td><td>{t3}</td><td>{wr}</td></tr>'
    html += '</table></div>'

    html += '<div class="card"><h2>每日Top10明细</h2><table><tr><th>Date</th><th>Code</th><th>Name</th><th>Rank</th><th>Score</th><th>Type</th><th>T+1</th><th>T+2</th><th>T+3</th></tr>'
    for r in daily_data:
        t1 = f"{r['t1_return']*100:+.2f}%" if r['t1_return'] is not None else '-'
        t2 = f"{r['t2_return']*100:+.2f}%" if r['t2_return'] is not None else '-'
        t3 = f"{r['t3_return']*100:+.2f}%" if r['t3_return'] is not None else '-'
        score = f"{r['final_score']:.1f}" if r['final_score'] is not None else '-'
        html += f'<tr><td>{r["trade_date"]}</td><td>{r["symbol"]}</td><td>{r["stock_name"] or ""}</td><td>{r["rank"]}</td><td>{score}</td><td>{r["setup_type"] or ""}</td><td>{t1}</td><td>{t2}</td><td>{t3}</td></tr>'
    html += '</table></div>'

    html += '</body></html>'

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(html)
    print(f"HTML report saved to: {output_path}")
    return output_path


def main():
    import argparse
    ap = argparse.ArgumentParser(description='xiaogu DB-First 复盘报表')
    ap.add_argument('--html', action='store_true', help='Generate HTML report')
    ap.add_argument('--output', type=str, help='HTML output path')
    args = ap.parse_args()

    print_terminal_report()

    if args.html:
        generate_html_report(args.output)


if __name__ == "__main__":
    main()
