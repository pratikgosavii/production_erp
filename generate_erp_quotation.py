"""
Generate ERP Development Quotation PDF — Rs. 2,50,000 total, no GST.
Matches the Quotation_ERP_Development.docx format.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, ListFlowable, ListItem
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
import os

# ── Colors ──
NAVY = HexColor('#132435')
STEEL = HexColor('#3f5c74')
INK = HexColor('#1a2432')
INK_SOFT = HexColor('#5b6b7a')
LINE = HexColor('#d7dde3')
PAPER = HexColor('#f5f7f9')
ROW_ALT = HexColor('#f8f9fb')

# ── Output ──
OUTPUT = os.path.join(os.path.dirname(__file__), 'Quotation_ERP_Development.pdf')

# ── Styles ──
s_normal = ParagraphStyle('Normal', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, alignment=TA_JUSTIFY)
s_small = ParagraphStyle('Small', fontName='Helvetica', fontSize=8, leading=11, textColor=INK_SOFT)
s_bold = ParagraphStyle('Bold', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=INK)
s_heading = ParagraphStyle('Heading', fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=NAVY, spaceBefore=14, spaceAfter=6)
s_subhead = ParagraphStyle('SubHead', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY)
s_right = ParagraphStyle('Right', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, alignment=TA_RIGHT)
s_right_bold = ParagraphStyle('RightBold', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=INK, alignment=TA_RIGHT)
s_center = ParagraphStyle('Center', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, alignment=TA_CENTER)
s_center_bold = ParagraphStyle('CenterBold', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=white, alignment=TA_CENTER)
s_title = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=22, leading=28, textColor=NAVY, alignment=TA_CENTER)
s_subtitle = ParagraphStyle('Subtitle', fontName='Helvetica', fontSize=11, leading=15, textColor=STEEL, alignment=TA_CENTER)
s_bullet = ParagraphStyle('Bullet', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, leftIndent=16, bulletIndent=4, spaceBefore=3)
s_scope = ParagraphStyle('Scope', fontName='Helvetica', fontSize=8.5, leading=12, textColor=INK)
s_scope_why = ParagraphStyle('ScopeWhy', fontName='Helvetica-Oblique', fontSize=8, leading=11, textColor=STEEL)
s_footer = ParagraphStyle('Footer', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT, alignment=TA_CENTER)


def build_pdf():
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )
    W = A4[0] - 40*mm  # usable width
    story = []

    # ═══════════════════════════════════════════
    # TITLE BLOCK
    # ═══════════════════════════════════════════
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('QUOTATION', s_title))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        'Design, Development, Testing &amp; Deployment of a<br/>'
        'Chemical Etching Works ERP',
        s_subtitle
    ))
    story.append(Spacer(1, 10*mm))

    # ── Prepared for / by ──
    meta_data = [
        [Paragraph('<b>Prepared for:</b>', s_bold),
         Paragraph('Chemical Etching Works Pvt. Ltd.', s_normal)],
        [Paragraph('<b>Prepared by:</b>', s_bold),
         Paragraph('Ayush Khade', s_normal)],
        [Paragraph('<b>Quotation No.:</b>', s_bold),
         Paragraph('QTN-DEV-001', s_normal)],
        [Paragraph('<b>Date:</b>', s_bold),
         Paragraph('09 Aug 2026', s_normal)],
        [Paragraph('<b>Valid for:</b>', s_bold),
         Paragraph('30 days', s_normal)],
    ]
    meta_table = Table(meta_data, colWidths=[W*0.22, W*0.78])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ('INNERGRID', (0,0), (-1,-1), 0.4, LINE),
        ('BACKGROUND', (0,0), (0,-1), PAPER),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10*mm))

    # ═══════════════════════════════════════════
    # SECTION 1 — What this covers
    # ═══════════════════════════════════════════
    story.append(Paragraph('1.  What this covers', s_heading))
    story.append(Paragraph(
        'This is a fixed-price quotation to build the role-based ERP described in the agreed '
        'Project Design Document — the full flow: Login → Quotation → Manager Approval '
        '→ PO &amp; Order Booking → Design → Production (job card) → QA → Dispatch. '
        'It is built on your existing Django application and reuses the existing database '
        '(customers, pricing, material and inventory) wherever possible.',
        s_normal
    ))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        '<b>Scope lock:</b> This price covers exactly the ten modules listed in section 2 as '
        'described. Any feature, screen, report or change not listed here is treated as a '
        'separate change request and quoted separately.',
        s_normal
    ))
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════
    # SECTION 2 — Module-wise scope & pricing
    # ═══════════════════════════════════════════
    story.append(Paragraph('2.  Module-wise scope &amp; pricing', s_heading))

    # Module data — adjusted to total Rs. 2,50,000
    modules = [
        (1, 'Foundation, Login &\nAccess Control',
         'Role-based login for 8 roles (Sales, Sales Manager, Design, Production Manager, '
         'Floor Manager, Stores, Accounts, QA), per-role landing dashboards and server-side '
         'access rules (e.g. prices hidden from Design). Base UI framework.',
         'Every other module sits on this and it controls who sees and does what.',
         14, 26000),
        (2, 'Master Data &\nSetup',
         'Reuse of existing reference tables (customers, price list, material / thickness / '
         'finish / etching, users) with light maintenance screens.',
         'Quotations, pricing and dropdowns depend on this data — mostly already in the database, so effort is low.',
         7, 12000),
        (3, 'Quotation, Approval\n& My Quotations',
         'Quotation form with automatic pricing, printable PDF, the salesperson\'s quotation '
         'list with versioning (v1/v2), Sales-Manager Approve / Reject-with-comment, and '
         '"Send to Customer" which e-mails the quote PDF automatically.',
         'The revenue entry point with a clean approval trail.',
         23, 42000),
        (4, 'Order Booking\n(Accounts)',
         'Accounts confirm the incoming PO, convert the quote to a Sales Order (SO number '
         'generated), and move it to Design. Order-booking dashboard with pipeline stage '
         'tracking and status badges; PO and drawing upload with revision history.',
         'Turns an approved quote + PO into a live, tracked order.',
         15, 28000),
        (5, 'Design & Query',
         'Design queue, design job detail, manual selection & reservation of a sheet/offcut '
         'from Stores, and a design-query thread routed back to the exact salesperson who '
         'raised the quotation.',
         'Turns an order into a production-ready job and closes drawing doubts quickly.',
         14, 26000),
        (6, 'Production &\nJob Card',
         'Ready-to-produce queue, priority 1–4 set by the Production Manager, a Station '
         'master (add / edit / remove stations), and a station-by-station job card where the '
         'Floor Manager starts the job, picks the applicable stations and marks each step '
         'complete — station time computed automatically.',
         'Runs the shop floor and records real throughput.',
         20, 36000),
        (7, 'Stores / Inventory',
         'Raw-material inventory of sheets and offcuts, goods-receipt (GRN) inward entry, '
         'issue / reserve against orders, offcut lineage and reorder-level alerts.',
         'Keeps stock accurate and prevents shortages that stall production.',
         12, 22000),
        (8, 'Quality Assurance\n(QA)',
         'QA queue of completed jobs, 4–5 structured inspection forms, document / photo '
         'upload, and retrieval of QA records by date and by customer.',
         'Records quality sign-off before dispatch and builds a searchable archive.',
         12, 22000),
        (9, 'Notifications &\nAudit Trail',
         'Handoff notifications between roles (design done → production, query raised → '
         'sales, etc.) and a full audit log of every status change and edit across all stages.',
         'Keeps work moving and makes every action traceable.',
         8, 18000),
        (10, 'Integration, UAT,\nDeployment & Training',
         'End-to-end integration, e-mail (SMTP) setup, data migration, user-acceptance '
         'testing with your team, go-live, staff training and handover, plus 30 days of '
         'post-go-live bug-fix support.',
         'Ensures the system works in daily use and your team can run it.',
         10, 18000),
    ]

    # Table header
    header = [
        Paragraph('<b>#</b>', s_center_bold),
        Paragraph('<b>Module</b>', s_center_bold),
        Paragraph('<b>Scope &amp; why it is needed</b>', s_center_bold),
        Paragraph('<b>Days</b>', s_center_bold),
        Paragraph('<b>Amount</b>', s_center_bold),
    ]
    rows = [header]

    total_days = 0
    total_amount = 0
    for num, name, scope, why, days, amount in modules:
        total_days += days
        total_amount += amount
        scope_para = Paragraph(
            f'{scope}<br/><i>WHY: {why}</i>',
            ParagraphStyle('sc', fontName='Helvetica', fontSize=8, leading=11, textColor=INK)
        )
        rows.append([
            Paragraph(str(num), s_center),
            Paragraph(f'<b>{name}</b>', ParagraphStyle('mn', fontName='Helvetica-Bold', fontSize=8.5, leading=11.5, textColor=NAVY)),
            scope_para,
            Paragraph(str(days), s_center),
            Paragraph(f'Rs. {amount:,}', s_right),
        ])

    # Total row
    rows.append([
        Paragraph('', s_center),
        Paragraph('<b>TOTAL</b>', ParagraphStyle('tot', fontName='Helvetica-Bold', fontSize=9.5, leading=13, textColor=NAVY)),
        Paragraph('<b>Complete system — design, development, testing &amp; go-live</b>',
                  ParagraphStyle('totd', fontName='Helvetica-Bold', fontSize=8.5, leading=12, textColor=NAVY)),
        Paragraph(f'<b>{total_days}</b>', ParagraphStyle('totc', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=NAVY, alignment=TA_CENTER)),
        Paragraph(f'<b>Rs. {total_amount:,}</b>',
                  ParagraphStyle('tota', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY, alignment=TA_RIGHT)),
    ])

    col_widths = [W*0.05, W*0.15, W*0.52, W*0.08, W*0.20]
    module_table = Table(rows, colWidths=col_widths, repeatRows=1)

    # Build style commands
    style_cmds = [
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('TEXTCOLOR', (0,0), (-1,0), white),
        ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ('INNERGRID', (0,0), (-1,-1), 0.4, LINE),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        # Total row
        ('BACKGROUND', (0,-1), (-1,-1), PAPER),
        ('LINEABOVE', (0,-1), (-1,-1), 1.5, NAVY),
        ('TOPPADDING', (0,-1), (-1,-1), 8),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 8),
    ]
    # Alternate row backgrounds
    for i in range(1, len(rows)-1):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), ROW_ALT))

    module_table.setStyle(TableStyle(style_cmds))
    story.append(module_table)
    story.append(Spacer(1, 4*mm))

    # Fixed project price line
    price_box = Table(
        [[Paragraph(
            f'<b>Fixed project price:  Rs. 2,50,000</b>  (excluding GST)',
            ParagraphStyle('fp', fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=NAVY, alignment=TA_CENTER)
        )]],
        colWidths=[W]
    )
    price_box.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1.5, NAVY),
        ('BACKGROUND', (0,0), (-1,-1), PAPER),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(price_box)
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════
    # SECTION 3 — Timeline
    # ═══════════════════════════════════════════
    story.append(Paragraph('3.  Timeline', s_heading))
    story.append(Paragraph(
        'Approximately 6 to 8 weeks to go-live, with modules delivered in the order above '
        'so you can review and pay per milestone.',
        s_normal
    ))
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════
    # SECTION 4 — Payment milestones
    # ═══════════════════════════════════════════
    story.append(Paragraph('4.  Payment milestones', s_heading))

    pay_header = [
        Paragraph('<b>Payment milestone</b>', s_center_bold),
        Paragraph('<b>%</b>', s_center_bold),
        Paragraph('<b>Amount</b>', s_center_bold),
    ]
    pay_rows = [pay_header]
    milestones = [
        ('On acceptance of this quotation (advance)', '25%', 62500),
        ('Foundation + Master Data + Quotation/Approval delivered', '20%', 50000),
        ('Order Booking + Design delivered', '20%', 50000),
        ('Production + Stores delivered', '20%', 50000),
        ('QA + UAT sign-off & go-live', '15%', 37500),
    ]
    for desc, pct, amt in milestones:
        pay_rows.append([
            Paragraph(desc, s_normal),
            Paragraph(f'<b>{pct}</b>', s_center),
            Paragraph(f'Rs. {amt:,}', s_right),
        ])
    # Total
    pay_rows.append([
        Paragraph('<b>Total</b>', s_bold),
        Paragraph('<b>100%</b>', ParagraphStyle('pc', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=NAVY, alignment=TA_CENTER)),
        Paragraph('<b>Rs. 2,50,000</b>', ParagraphStyle('pa', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=NAVY, alignment=TA_RIGHT)),
    ])

    pay_table = Table(pay_rows, colWidths=[W*0.55, W*0.15, W*0.30])
    pay_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('TEXTCOLOR', (0,0), (-1,0), white),
        ('BOX', (0,0), (-1,-1), 0.75, LINE),
        ('INNERGRID', (0,0), (-1,-1), 0.4, LINE),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,-1), (-1,-1), PAPER),
        ('LINEABOVE', (0,-1), (-1,-1), 1.2, NAVY),
    ]))
    story.append(pay_table)
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════
    # SECTION 5 — Assumptions
    # ═══════════════════════════════════════════
    story.append(Paragraph('5.  Assumptions', s_heading))

    assumptions = [
        'Built on your existing Django application and database; existing tables (customers, '
        'price list, material / thickness / finish, inventory) are reused.',
        'Scope is per the Project Design Document dated 08 Aug 2026. Anything outside it is a '
        'separate change request (see Scope lock above).',
        'Server / hosting, domain, and the e-mail (SMTP) service used for "Send to Customer" '
        'are arranged and paid for by the client.',
        'Includes 30 days of post-go-live bug-fix support. Ongoing maintenance (AMC) is offered '
        'separately at approx. 15–18% of project value per year.',
        'Content decisions still pending (e.g. the exact QA inspection forms) to be confirmed by '
        'the client before that module is built.',
    ]
    for text in assumptions:
        story.append(Paragraph(
            f'•  {text}',
            ParagraphStyle('bl', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, leftIndent=12, bulletIndent=0, spaceBefore=4)
        ))
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════
    # SECTION 6 — Not included
    # ═══════════════════════════════════════════
    story.append(Paragraph('6.  Not included (excluded from this price)', s_heading))

    exclusions = [
        'Automatic nesting calculation (parts-per-sheet / yield / auto-offcut) — the designer selects a sheet manually.',
        'Cost / benchmark / margin analytics approval dashboard.',
        'Dashboard KPI summary tiles.',
        'OTP / SMS login (login is username + password).',
        'Mobile apps, hardware, RFID scanners, SMS/e-mail gateway subscription, and any third-party licence costs.',
    ]
    for text in exclusions:
        story.append(Paragraph(
            f'•  {text}',
            ParagraphStyle('bl2', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, leftIndent=12, bulletIndent=0, spaceBefore=4)
        ))
    story.append(Spacer(1, 12*mm))

    # ═══════════════════════════════════════════
    # SIGNATURE BLOCK
    # ═══════════════════════════════════════════
    story.append(Paragraph('<b>Accepted for Chemical Etching Works Pvt. Ltd.</b>', s_bold))
    story.append(Spacer(1, 15*mm))

    sig_table = Table([
        [Paragraph('Signature: ____________________________', s_normal),
         Paragraph('Name: ____________________', s_normal),
         Paragraph('Date: ____________', s_normal)],
    ], colWidths=[W*0.40, W*0.35, W*0.25])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
        ('LINEABOVE', (0,0), (-1,0), 0.75, LINE),
        ('TOPPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 8*mm))

    # Footer note
    story.append(Paragraph(
        '<i>This fixed-price quotation is valid for 30 days and is based on the agreed scope; '
        'changes to scope are quoted separately.</i>',
        ParagraphStyle('fn', fontName='Helvetica-Oblique', fontSize=8, leading=11, textColor=INK_SOFT, alignment=TA_CENTER)
    ))

    # ── Build ──
    doc.build(story)
    print(f'PDF generated: {OUTPUT}')


if __name__ == '__main__':
    build_pdf()
