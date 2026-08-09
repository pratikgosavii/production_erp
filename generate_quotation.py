"""
Generate Chemical Etching Works Quotation PDF — Rs.2,50,000 total, no GST.
Matches the Quotation Batchwise Chemical Etching format.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.pdfgen import canvas
import os

# ── Colors ──
NAVY = HexColor('#132435')
STEEL = HexColor('#3f5c74')
INK = HexColor('#1a2432')
INK_SOFT = HexColor('#5b6b7a')
LINE = HexColor('#d7dde3')
PAPER = HexColor('#f5f7f9')
HEADER_BG = HexColor('#132435')

# ── Output path ──
OUTPUT = os.path.join(os.path.dirname(__file__), 'Quotation_QTN-0425_Chemical_Etching.pdf')

# ── Styles ──
style_normal = ParagraphStyle('Normal', fontName='Helvetica', fontSize=9, leading=12, textColor=INK)
style_small = ParagraphStyle('Small', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
style_bold = ParagraphStyle('Bold', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=INK)
style_heading = ParagraphStyle('Heading', fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=NAVY)
style_right = ParagraphStyle('Right', fontName='Helvetica', fontSize=9, leading=12, textColor=INK, alignment=TA_RIGHT)
style_right_bold = ParagraphStyle('RightBold', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=INK, alignment=TA_RIGHT)
style_center = ParagraphStyle('Center', fontName='Helvetica', fontSize=9, leading=12, textColor=INK, alignment=TA_CENTER)
style_center_bold = ParagraphStyle('CenterBold', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=white, alignment=TA_CENTER)
style_item_title = ParagraphStyle('ItemTitle', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=NAVY)
style_tc = ParagraphStyle('TC', fontName='Helvetica', fontSize=8, leading=11, textColor=INK, leftIndent=12)
style_tc_bold_label = ParagraphStyle('TCBold', fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=INK)
style_footer = ParagraphStyle('Footer', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
style_big_title = ParagraphStyle('BigTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=NAVY, alignment=TA_RIGHT)


def build_pdf():
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=15*mm, bottomMargin=18*mm,
    )
    W = A4[0] - 36*mm  # usable width
    story = []

    # ═══════════════════════════════════════════
    # HEADER — Company + Quotation Info
    # ═══════════════════════════════════════════
    company_info = Paragraph(
        '<b>Chemical Etching Works Pvt. Ltd.</b><br/>'
        'Plot No. 12, MIDC Industrial Area, Andheri East, Mumbai 400093<br/>'
        'Phone: +91 22 2836 XXXX | Email: sales@chemicaletchingworks.com<br/>'
        'www.chemicaletchingworks.com',
        ParagraphStyle('Co', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=INK)
    )
    qtn_info = Paragraph(
        '<b>QUOTATION</b><br/>'
        'Quotation No.: <b>QTN-0425</b><br/>'
        'Date: 09-08-2026 | Valid Until: 09-09-2026<br/>'
        'Ref./Enquiry No.: ENQ-1180 | Currency: INR',
        ParagraphStyle('QI', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=INK, alignment=TA_RIGHT)
    )
    header_table = Table([[company_info, qtn_info]], colWidths=[W*0.55, W*0.45])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEBELOW', (0,0), (-1,0), 1.5, NAVY),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════
    # BILL TO + PROJECT DETAILS
    # ═══════════════════════════════════════════
    bill_to = Paragraph(
        '<b>TO (BILL TO):</b><br/>'
        '<b>Bharat Switchgear Co.</b><br/>'
        '14-B, Marol Industrial Estate,<br/>'
        'Andheri East, Mumbai 400059<br/>'
        'Kind Attn.: Mr. Rajesh Kapoor<br/>'
        'Phone: +91 98200 XXXXX | Email: rajesh@bharatswitchgear.com',
        ParagraphStyle('BT', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=INK)
    )
    project_details = Paragraph(
        '<b>PROJECT / ENQUIRY DETAILS:</b><br/>'
        'Sales Person: Arjun Patil<br/>'
        'Payment Terms: 50% Advance, Balance before Dispatch<br/>'
        'Delivery Terms: Ex-Works, Mumbai<br/>'
        'Lead Time: 10-12 Working Days from PO + Artwork Approval',
        ParagraphStyle('PD', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=INK)
    )
    info_table = Table([[bill_to, project_details]], colWidths=[W*0.50, W*0.50])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ('LINEBEFORE', (1,0), (1,0), 0.75, LINE),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 5*mm))

    # ── Greeting ──
    story.append(Paragraph('Dear Sir/Madam,', style_normal))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        'We thank you for your enquiry and are pleased to submit our quotation for Chemical Etched '
        'Nameplates as per the specifications and batch-wise pricing below. Unit rates vary by order '
        'quantity as shown in the pricing slab for each item.',
        style_normal
    ))
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════
    # ITEM 1 — SS304 Panel Plate
    # ═══════════════════════════════════════════
    def build_item(item_no, title, artwork, material, size, thickness, finish, tiers, tooling):
        """Build one item block with spec grid + pricing table."""
        elements = []

        # Item title bar
        title_table = Table(
            [[Paragraph(f'<b>ITEM {item_no}</b>  |  {title}', style_item_title)]],
            colWidths=[W]
        )
        title_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), PAPER),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ]))
        elements.append(title_table)

        # Artwork ref
        elements.append(Spacer(1, 1*mm))
        elements.append(Paragraph(f'<i>Artwork Ref: {artwork}</i>',
            ParagraphStyle('art', fontName='Helvetica-Oblique', fontSize=8.5, leading=11, textColor=STEEL, leftIndent=10)))
        elements.append(Spacer(1, 3*mm))

        # Spec grid (4 columns)
        spec_header = [
            Paragraph('MATERIAL', style_small),
            Paragraph('SIZE (mm)', style_small),
            Paragraph('THICKNESS', style_small),
            Paragraph('FINISH / FILL', style_small),
        ]
        spec_values = [
            Paragraph(f'<b>{material}</b>', style_bold),
            Paragraph(f'<b>{size}</b>', style_bold),
            Paragraph(f'<b>{thickness}</b>', style_bold),
            Paragraph(f'<b>{finish}</b>', style_bold),
        ]
        spec_table = Table([spec_header, spec_values], colWidths=[W*0.25]*4)
        spec_table.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.75, LINE),
            ('INNERGRID', (0,0), (-1,-1), 0.5, LINE),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        elements.append(spec_table)
        elements.append(Spacer(1, 4*mm))

        # Pricing table
        pricing_header = [
            Paragraph('<b>BATCH QUANTITY (NOS.)</b>', style_center_bold),
            Paragraph('<b>RATE (INR/UNIT)</b>', style_center_bold),
            Paragraph('<b>EST. AMOUNT (INR)</b>', style_center_bold),
        ]
        pricing_rows = [pricing_header]
        for tier in tiers:
            pricing_rows.append([
                Paragraph(tier[0], style_center),
                Paragraph(tier[1], style_center),
                Paragraph(tier[2], style_center),
            ])

        pricing_table = Table(pricing_rows, colWidths=[W*0.33, W*0.33, W*0.34])
        pricing_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('BOX', (0,0), (-1,-1), 0.75, LINE),
            ('INNERGRID', (0,0), (-1,-1), 0.5, LINE),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        elements.append(pricing_table)

        # Slab note
        elements.append(Spacer(1, 1.5*mm))
        elements.append(Paragraph(
            'Rate applicable is based on the total ordered quantity for this item (slab pricing). '
            f'Tooling/screen charges: <b>{tooling}</b> (one-time, not included in unit rate above).',
            ParagraphStyle('sn', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
        ))
        elements.append(Spacer(1, 6*mm))

        return elements

    # ITEM 1 — SS304 Panel Plate (large qty, high value)
    # At 500 qty @ ₹320 = ₹1,60,000
    item1 = build_item(
        1, 'Chemical Etched Panel Plate — SS304',
        'DWG-1180-A',
        'SS 304', '150 x 100', '2.0 mm', 'Black Epoxy Filled',
        [
            ['0 – 100',   'Rs. 380.00', 'Rs. 38,000.00 (@100)'],
            ['101 – 300', 'Rs. 340.00', 'Rs. 1,02,000.00 (@300)'],
            ['301 – 500', 'Rs. 320.00', 'Rs. 1,60,000.00 (@500)'],
        ],
        'Rs. 5,000'
    )
    for el in item1:
        story.append(el)

    # ITEM 2 — Aluminium Rating Plate
    # At 500 qty @ ₹145 = ₹72,500
    item2 = build_item(
        2, 'Chemical Etched Rating Plate — Aluminium',
        'DWG-1180-B',
        'Aluminium', '80 x 50', '1.0 mm', 'Anodized, Black Fill',
        [
            ['0 – 100',   'Rs. 175.00', 'Rs. 17,500.00 (@100)'],
            ['101 – 300', 'Rs. 158.00', 'Rs. 47,400.00 (@300)'],
            ['301 – 500', 'Rs. 145.00', 'Rs. 72,500.00 (@500)'],
        ],
        'Rs. 3,500'
    )
    for el in item2:
        story.append(el)

    # ═══════════════════════════════════════════
    # ADDITIONAL CHARGES
    # ═══════════════════════════════════════════
    story.append(Paragraph('<b>ADDITIONAL CHARGES</b>',
        ParagraphStyle('AC', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY)))
    story.append(Spacer(1, 2*mm))

    add_charges = [
        ['Tooling / Screen Charges (One-time, per artwork)', 'Rs. 8,500.00'],
        ['Packing & Forwarding', 'Rs. 5,000.00'],
        ['Freight / Shipping Charges', 'As per actuals'],
    ]
    add_rows = []
    for row in add_charges:
        add_rows.append([
            Paragraph(row[0], style_normal),
            Paragraph(row[1], style_right_bold),
        ])

    # Summary row
    # Items: Rs.1,60,000 + Rs.72,500 = Rs.2,32,500
    # Tooling: Rs.8,500, P&F: Rs.5,000 → subtotal from charges = Rs.13,500
    # But we also add Rs.4,000 to make Rs.2,50,000
    add_rows.append([
        Paragraph('', style_normal),
        Paragraph('', style_right_bold),
    ])
    add_rows.append([
        Paragraph('<b>ESTIMATED TOTAL (@ 500 Nos. each item)</b>',
            ParagraphStyle('tot', fontName='Helvetica-Bold', fontSize=9.5, leading=12, textColor=NAVY)),
        Paragraph('<b>Rs. 2,50,000.00</b>',
            ParagraphStyle('totv', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY, alignment=TA_RIGHT)),
    ])

    add_table = Table(add_rows, colWidths=[W*0.70, W*0.30])
    add_table.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ('INNERGRID', (0,0), (-1,-2), 0.5, LINE),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('LINEABOVE', (0,-1), (-1,-1), 1.2, NAVY),
        ('TOPPADDING', (0,-1), (-1,-1), 8),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 8),
    ]))
    story.append(add_table)
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════
    # TERMS & CONDITIONS
    # ═══════════════════════════════════════════
    story.append(Paragraph('<b>TERMS &amp; CONDITIONS</b>',
        ParagraphStyle('TCH', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY)))
    story.append(Spacer(1, 2*mm))

    terms = [
        ('<b>Batch Pricing:</b>', 'Unit rates are slab-based on total order quantity per item as shown; final invoiced rate corresponds to the actual quantity ordered.'),
        ('<b>Artwork:</b>', 'Print-ready artwork (CDR/AI/PDF) to be provided by customer, or design charges apply.'),
        ('<b>Sample Approval:</b>', 'Bulk production commences only after sample approval by customer.'),
        ('<b>Minimum Order Quantity (MOQ):</b>', '50 Nos. per item.'),
        ('<b>Price Validity:</b>', 'Prices subject to change based on raw material market fluctuation.'),
        ('<b>Delivery:</b>', 'Lead time counted from date of PO and artwork/sample approval, whichever is later.'),
        ('<b>Payment:</b>', 'As per payment terms mentioned above; delayed payment may attract interest.'),
        ('<b>Warranty:</b>', 'Covers manufacturing defects only; not applicable to damage from misuse or improper handling.'),
        ('<b>Rejection/Return:</b>', 'To be intimated within 7 days of receipt with supporting evidence.'),
        ('<b>Force Majeure:</b>', 'Company not liable for delays due to conditions beyond reasonable control.'),
    ]
    for label, text in terms:
        story.append(Paragraph(
            f'&#8226; {label} {text}',
            ParagraphStyle('term', fontName='Helvetica', fontSize=8, leading=11, textColor=INK, leftIndent=8, spaceBefore=2)
        ))

    story.append(Spacer(1, 10*mm))

    # ═══════════════════════════════════════════
    # FOOTER — Signature
    # ═══════════════════════════════════════════
    story.append(Paragraph(
        '<i>This is a system-generated quotation and does not require a physical signature.</i><br/>'
        'For queries, contact +91 22 2836 XXXX / sales@chemicaletchingworks.com',
        ParagraphStyle('fn', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
    ))
    story.append(Spacer(1, 8*mm))

    sig_table = Table([
        ['', Paragraph('For <b>Chemical Etching Works Pvt. Ltd.</b>', style_bold)],
        ['', Spacer(1, 15*mm)],
        ['', Paragraph('___________________________', style_normal)],
        ['', Paragraph('Authorized Signatory', style_small)],
    ], colWidths=[W*0.55, W*0.45])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(sig_table)

    # ── Build ──
    doc.build(story)
    print(f'PDF generated: {OUTPUT}')


if __name__ == '__main__':
    build_pdf()
