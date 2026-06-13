from __future__ import annotations

from html import escape
from pathlib import Path

from airmoney.storage.repositories import Repository


def export_candidates_html(path: str | Path, repo: Repository | None = None) -> Path:
    repository = repo or Repository()
    rows = repository.list_candidates(limit=100000)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        "<tr>"
        f"<td>{escape(str(row.get('recommendation_level', '')))}</td>"
        f"<td>{escape(str(row.get('risk_adjusted_score') or row.get('anomaly_score') or row.get('recommendation_score') or ''))}</td>"
        f"<td>{escape(str(row.get('status', '')))}</td>"
        f"<td>{escape(str(row.get('skin_name', '')))}</td>"
        f"<td>{escape(str(row.get('collection_name', '')))}</td>"
        f"<td>{escape(str(row.get('buy_price_rub', '')))}</td>"
        f"<td>{escape(str(row.get('exit_price_rub') or row.get('estimated_resale_price_rub') or ''))}</td>"
        f"<td>{escape(str(row.get('estimated_net_resale_rub', '')))}</td>"
        f"<td>{escape(str(row.get('estimated_profit_rub', '')))}</td>"
        f"<td>{escape(str(row.get('estimated_roi_percent', '')))}</td>"
        f"<td>{escape(str(row.get('market_confidence', '')))}</td>"
        f"<td>{escape(str(row.get('requires_sweep', '')))}</td>"
        f"<td>{escape(str(row.get('capital_required_rub', '')))}</td>"
        f"<td>{escape(str(row.get('pack_size', '')))}</td>"
        f"<td>{escape(str(row.get('pack_cost_rub', '')))}</td>"
        f"<td>{escape(str(row.get('substitute_cap_rub', '')))}</td>"
        f"<td>{escape(str(row.get('float_value', '')))}</td>"
        f"<td>{escape(str(row.get('float_bucket', '')))}</td>"
        f"<td>{escape(str(row.get('fair_price_rub', '')))}</td>"
        f"<td>{escape(str(row.get('local_median_rub', '')))}</td>"
        f"<td>{escape(str(row.get('float_peer_median_rub', '')))}</td>"
        f"<td>{escape(str(row.get('pattern', '')))}</td>"
        f"<td>{escape(str(row.get('currency_source', '')))} {escape(str(row.get('currency_fetched_at', '')))}</td>"
        f"<td>{escape(str(row.get('created_at', '')))}</td>"
        "</tr>"
        for row in rows
    )
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Airmoney candidates</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f0f3f5; }}
  </style>
</head>
<body>
  <h1>Кандидаты</h1>
  <table>
    <thead>
      <tr>
        <th>Уровень</th><th>Score</th><th>Статус</th><th>Скин</th><th>Коллекция</th>
        <th>Покупка</th><th>Exit</th><th>Чистыми</th><th>Профит</th>
        <th>ROI</th><th>Conf</th><th>Sweep</th><th>Capital</th><th>Pack size</th><th>Pack cost</th><th>Sub cap</th>
        <th>Float</th><th>Bucket</th><th>Fair</th><th>Local</th>
        <th>Float peer</th><th>Pattern</th><th>Курс</th><th>Найден</th>
      </tr>
    </thead>
    <tbody>{body}</tbody>
  </table>
</body>
</html>"""
    output.write_text(html, encoding="utf-8")
    return output
