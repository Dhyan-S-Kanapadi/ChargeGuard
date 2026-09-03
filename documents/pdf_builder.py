import hashlib
import json
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _text(value: Any) -> str:
    return escape("" if value is None else str(value))


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CoverTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17212B"),
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#17212B"),
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    return styles


def _footer(pdf_canvas: canvas.Canvas, doc) -> None:
    pdf_canvas.saveState()
    pdf_canvas.setFont("Helvetica", 8)
    pdf_canvas.setFillColor(colors.HexColor("#5B6570"))
    pdf_canvas.drawString(18 * mm, 12 * mm, "ChargeGuard dispute evidence packet")
    pdf_canvas.drawRightString(192 * mm, 12 * mm, f"Page {doc.page}")
    pdf_canvas.restoreState()


def build_rebuttal_pdf(
    packet: dict[str, Any],
    output_path: str | Path,
    *,
    template_text: str,
) -> Path:
    """Build a deterministic filing PDF from an already verified fact packet."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"Chargeback {packet['chargeback_id']}",
        author="ChargeGuard AI",
        subject="Chargeback representment evidence",
    )

    story = [
        Spacer(1, 28 * mm),
        Paragraph("CHARGEBACK REPRESENTMENT", styles["CoverTitle"]),
        Paragraph(
            f"{_text(packet['card_network'])} reason code {_text(packet['reason_code'])}",
            styles["Heading2"],
        ),
        Spacer(1, 10 * mm),
    ]
    cover_rows = [
        ["Chargeback ID", _text(packet["chargeback_id"])],
        ["Merchant", _text(packet["merchant"])],
        ["Disputed amount", f"{_text(packet['currency'])} {float(packet['amount']):.2f}"],
        ["Reason", _text(packet.get("reason_name", packet["reason_code"]))],
    ]
    cover_table = Table(cover_rows, colWidths=[45 * mm, 100 * mm])
    cover_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E9EEF2")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB4BD")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([cover_table, Spacer(1, 12 * mm), Paragraph(_text(template_text), styles["BodyText"]), PageBreak()])

    for section in packet.get("sections", []):
        story.append(Paragraph(_text(section.get("title")), styles["SectionTitle"]))
        story.append(Paragraph(_text(section.get("body")), styles["BodyText"]))
        story.append(Spacer(1, 4 * mm))

    ce3_rows = packet.get("ce3_qualified_transaction_data", [])
    if ce3_rows:
        story.append(Paragraph("Visa CE3.0 Qualified Transaction Data", styles["SectionTitle"]))
        data = [["Prior transaction reference", "Matching qualified elements"]]
        data.extend(
            [
                _text(row.get("prior_transaction_ref")),
                _text(", ".join(row.get("matched_elements", []))),
            ]
            for row in ce3_rows
        )
        ce3_table = Table(data, colWidths=[65 * mm, 100 * mm], repeatRows=1)
        ce3_table.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE5EB")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB4BD")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend([ce3_table, Spacer(1, 4 * mm)])

    story.append(Paragraph("Evidence index", styles["SectionTitle"]))
    evidence_rows = [["Evidence type", "Included"]]
    for name in packet.get("evidence_priority", packet.get("evidence_status", {})):
        included = bool(packet.get("evidence_status", {}).get(name))
        evidence_rows.append([name.replace("_", " ").title(), "Yes" if included else "No"])
    evidence_table = Table(evidence_rows, colWidths=[90 * mm, 35 * mm], repeatRows=1)
    evidence_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE5EB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB4BD")),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(evidence_table)

    packet_digest = hashlib.sha256(
        json.dumps(packet, default=str, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32].encode("ascii")

    def deterministic_canvas(filename, **kwargs):
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 1
        pdf_canvas = canvas.Canvas(filename, **kwargs)
        pdf_canvas._doc._ID = (
            b"\n[<" + packet_digest + b"><" + packet_digest + b">]\n"
            b"% ChargeGuard deterministic document identifier\n"
        )
        return pdf_canvas

    document.build(
        story,
        onFirstPage=_footer,
        onLaterPages=_footer,
        canvasmaker=deterministic_canvas,
    )
    return path
