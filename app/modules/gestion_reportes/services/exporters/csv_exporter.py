import csv
import io

from .cell_security import safe_spreadsheet_value


def export_csv(title, columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([column["label"] for column in columns])
    for row in rows:
        writer.writerow([
            safe_spreadsheet_value(row.get(column["key"])) for column in columns
        ])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")
