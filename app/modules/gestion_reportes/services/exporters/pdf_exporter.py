from datetime import datetime, timezone
from io import BytesIO

def export_pdf(title, columns, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    data = [[column["label"] for column in columns]]
    data.extend([[str(row.get(column["key"]) or "") for column in columns] for row in rows])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    doc.build([Paragraph(title, styles["Title"]), Paragraph(datetime.now(timezone.utc).isoformat(), styles["Normal"]), Spacer(1, 12), table])
    return output.getvalue()
