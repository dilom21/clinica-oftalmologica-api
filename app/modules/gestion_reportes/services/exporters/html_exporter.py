from datetime import datetime, timezone
from html import escape


def export_html(title, columns, rows):
    generated_at = datetime.now(timezone.utc).isoformat()
    safe_title = escape(str(title), quote=True)
    safe_date = escape(generated_at, quote=True)
    headers = "".join(f"<th>{escape(str(column['label']), quote=True)}</th>" for column in columns)

    def cell_value(row, column):
        value = row.get(column["key"])
        return escape("" if value is None else str(value), quote=True)

    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{cell_value(row, column)}</td>"
            for column in columns
        )
        + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ margin: 0; padding: 24px; color: #1f2937; background: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }}
    main {{ max-width: 1200px; margin: 0 auto; background: #fff; padding: 24px; border: 1px solid #dbe3ee; }}
    h1 {{ margin: 0 0 8px; color: #123b63; font-size: 1.5rem; }}
    .generated {{ color: #64748b; margin: 0 0 20px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 9px 10px; text-align: left; vertical-align: top; border: 1px solid #cbd5e1; }}
    th {{ color: #123b63; background: #e8eef5; }}
    tr:nth-child(even) {{ background: #f8fafc; }}
    footer {{ margin-top: 16px; color: #475569; font-size: .9rem; }}
    @media print {{ body {{ padding: 0; background: #fff; }} main {{ border: 0; }} }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    <p class="generated">Generado: {safe_date}</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{headers}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    <footer>Cantidad de registros: {len(rows)}</footer>
  </main>
</body>
</html>""".encode("utf-8")
