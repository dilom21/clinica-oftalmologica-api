import csv
import io


def export_csv(title, columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([column["label"] for column in columns])
    for row in rows:
        writer.writerow([row.get(column["key"]) for column in columns])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")
