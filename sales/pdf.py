"""The printable quotation — the document the customer actually receives.

Layout, colours and section order are lifted from ``generate_quotation.py`` at
the project root (the working reportlab reference build); everything that was
hard-coded there is read off the real ``Quotation`` here.

MONEY RENDERING — deliberate: every figure is written "Rs. 1,60,000.00", never
with the rupee sign. The built-in Helvetica face has no glyph for U+20B9 and
reportlab prints a black box in its place. Do not swap it back unless a TTF
carrying the glyph is registered first.
"""
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Colours (same palette as the reference build) ──
NAVY = HexColor('#132435')
STEEL = HexColor('#3f5c74')
INK = HexColor('#1a2432')
INK_SOFT = HexColor('#5b6b7a')
LINE = HexColor('#d7dde3')
PAPER = HexColor('#f5f7f9')
GREEN_BG = HexColor('#e6f4ea')
GREEN = HexColor('#1e7c3f')

# ── Company block. The ERP has no company master, so anything beyond the name
# is optional and only printed when the deployment supplies it in settings. ──
COMPANY_NAME = getattr(settings, 'COMPANY_NAME', 'Ravi Raj Anodisers')
COMPANY_LINES = [
    getattr(settings, 'COMPANY_ADDRESS', ''),
    ' | '.join(bit for bit in (
        f'Phone: {getattr(settings, "COMPANY_PHONE", "")}'
        if getattr(settings, 'COMPANY_PHONE', '') else '',
        f'Email: {getattr(settings, "COMPANY_EMAIL", "")}'
        if getattr(settings, 'COMPANY_EMAIL', '') else '',
    ) if bit),
    getattr(settings, 'COMPANY_GSTIN', '') and f'GSTIN: {settings.COMPANY_GSTIN}',
    getattr(settings, 'COMPANY_WEBSITE', ''),
]

# The clause list is the one rendered on templates/sales/quotation_form.html —
# what is printed has to match what the salesperson saw on screen.
TERMS = [
    ('Batch Pricing', 'Unit rates are slab-based on total order quantity per item as shown; '
                      'final invoiced rate corresponds to the actual quantity ordered.'),
    ('Artwork', 'Print-ready artwork (CDR/AI/PDF) to be provided by customer, or design '
                'charges apply.'),
    ('Sample Approval', 'Bulk production commences only after sample approval by customer.'),
    ('Minimum Order Quantity (MOQ)', 'As specified per item, if any.'),
    ('Price Validity', 'Prices subject to change based on raw material market fluctuation '
                       'and currency exchange rate.'),
    ('Delivery', 'Lead time counted from date of PO and artwork/sample approval, '
                 'whichever is later.'),
    ('Payment', 'As per payment terms mentioned above; delayed payment may attract interest.'),
    ('Warranty', 'Covers manufacturing defects only; not applicable to damage from misuse '
                 'or improper handling.'),
    ('Rejection/Return', 'To be intimated within 7 days of receipt with supporting evidence.'),
    ('Force Majeure', 'Company not liable for delays due to conditions beyond '
                      'reasonable control.'),
]

# ── Styles ──
S_NORMAL = ParagraphStyle('N', fontName='Helvetica', fontSize=9, leading=12, textColor=INK)
S_SMALL = ParagraphStyle('S', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
S_BOLD = ParagraphStyle('B', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=INK)
S_RIGHT_BOLD = ParagraphStyle('RB', fontName='Helvetica-Bold', fontSize=9, leading=12,
                              textColor=INK, alignment=TA_RIGHT)
S_CENTER = ParagraphStyle('C', fontName='Helvetica', fontSize=9, leading=12,
                          textColor=INK, alignment=TA_CENTER)
S_CENTER_APPLIES = ParagraphStyle('CA', fontName='Helvetica-Bold', fontSize=9, leading=12,
                                  textColor=GREEN, alignment=TA_CENTER)
S_HEAD_CELL = ParagraphStyle('HC', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
                             textColor=white, alignment=TA_CENTER)
S_ITEM_TITLE = ParagraphStyle('IT', fontName='Helvetica-Bold', fontSize=10.5, leading=13,
                              textColor=NAVY)
S_SECTION = ParagraphStyle('SEC', fontName='Helvetica-Bold', fontSize=10, leading=13,
                           textColor=NAVY)
S_BLOCK = ParagraphStyle('BL', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=INK)
S_BLOCK_R = ParagraphStyle('BLR', fontName='Helvetica', fontSize=8.5, leading=11.5,
                           textColor=INK, alignment=TA_RIGHT)
S_ART = ParagraphStyle('ART', fontName='Helvetica-Oblique', fontSize=8.5, leading=11,
                       textColor=STEEL, leftIndent=10)
S_NOTE = ParagraphStyle('NT', fontName='Helvetica', fontSize=7.5, leading=10, textColor=INK_SOFT)
S_TERM = ParagraphStyle('T', fontName='Helvetica', fontSize=8, leading=11, textColor=INK,
                        leftIndent=8, spaceBefore=2)
S_TOTAL_L = ParagraphStyle('TL', fontName='Helvetica-Bold', fontSize=9.5, leading=12,
                           textColor=NAVY)
S_TOTAL_V = ParagraphStyle('TV', fontName='Helvetica-Bold', fontSize=10, leading=13,
                           textColor=NAVY, alignment=TA_RIGHT)


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------
def _dec(value):
    try:
        return Decimal(value or 0)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal('0')


def _group(text):
    """'160000' -> '1,60,000'. Indian grouping, matching the on-screen money."""
    if len(text) <= 3:
        return text
    head, tail = text[:-3], text[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ','.join(groups + [tail])


def _rs(value):
    """'Rs. 1,60,000.00'. NEVER the rupee sign — Helvetica has no glyph for it."""
    amount = _dec(value).quantize(Decimal('0.01'))
    sign = '-' if amount < 0 else ''
    whole, _, paise = f'{abs(amount):.2f}'.partition('.')
    return f'{sign}Rs. {_group(whole)}.{paise}'


def _txt(value):
    """Escaped text for a Paragraph; None/blank collapse to ''."""
    return escape(str(value)) if value not in (None, '') else ''


def _date(value):
    return value.strftime('%d-%m-%Y') if value else ''


def _lines(*parts):
    """Join the parts that actually carry a value into a <br/> block."""
    return '<br/>'.join(p for p in parts if p)


def _tier_range(tier):
    if tier.qty_to:
        return f'{tier.qty_from} &#8211; {tier.qty_to}'
    return f'{tier.qty_from} and above'


# ---------------------------------------------------------------------------
# Story sections
# ---------------------------------------------------------------------------
def _header(quotation, width):
    company = Paragraph(
        _lines(f'<b>{_txt(COMPANY_NAME)}</b>', *(_txt(l) for l in COMPANY_LINES)),
        S_BLOCK,
    )
    info = Paragraph(
        _lines(
            '<b>QUOTATION</b>',
            f'Quotation No.: <b>{_txt(quotation.number)}</b> &#183; '
            f'Version: <b>v{quotation.version}</b>',
            f'Date: {_date(quotation.quote_date)}'
            + (f' | Valid Until: {_date(quotation.valid_until)}'
               if quotation.valid_until else ''),
            (f'Ref./Enquiry No.: {_txt(quotation.enquiry_ref)} | ' if quotation.enquiry_ref else '')
            + f'Currency: {_txt(quotation.currency)}',
        ),
        S_BLOCK_R,
    )
    table = Table([[company, info]], colWidths=[width * 0.55, width * 0.45])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, NAVY),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
    ]))
    return table


def _parties(quotation, width):
    customer = quotation.customer
    bill_to = Paragraph(
        _lines(
            '<b>TO (BILL TO):</b>',
            f'<b>{_txt(getattr(customer, "name", "")) or "Customer not on file"}</b>',
            _txt(getattr(customer, 'address', '')),
            f'GSTIN: {_txt(customer.gstin)}' if getattr(customer, 'gstin', '') else '',
            f'Kind Attn.: {_txt(customer.contact_person)}'
            if getattr(customer, 'contact_person', '') else '',
            ' | '.join(bit for bit in (
                f'Phone: {_txt(customer.phone)}' if getattr(customer, 'phone', '') else '',
                f'Email: {_txt(customer.email)}' if getattr(customer, 'email', '') else '',
            ) if bit),
        ),
        S_BLOCK,
    )
    project = Paragraph(
        _lines(
            '<b>PROJECT / ENQUIRY DETAILS:</b>',
            f'Sales Person: {_txt(getattr(quotation.salesperson, "short_name", ""))}',
            f'Payment Terms: {_txt(quotation.payment_terms)}' if quotation.payment_terms else '',
            f'Delivery Terms: {_txt(quotation.delivery_terms)}' if quotation.delivery_terms else '',
            f'Lead Time: {_txt(quotation.lead_time)}' if quotation.lead_time else '',
        ),
        S_BLOCK,
    )
    table = Table([[bill_to, project]], colWidths=[width * 0.50, width * 0.50])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.75, LINE),
        ('LINEBEFORE', (1, 0), (1, 0), 0.75, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    return table


def _item_block(item, width):
    """One line item: title, artwork, spec grid and its FULL price ladder."""
    elements = []

    code = item.item_code.code if item.item_code_id and item.item_code else ''
    heading = ' | '.join(bit for bit in (
        f'ITEM {item.position}', _txt(code), _txt(item.description)) if bit)
    title = Table([[Paragraph(f'<b>{heading}</b>', S_ITEM_TITLE)]], colWidths=[width])
    title.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PAPER),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('BOX', (0, 0), (-1, -1), 0.75, LINE),
    ]))

    head = [Paragraph(h, S_SMALL) for h in
            ('MATERIAL', 'THICKNESS', 'SIZE (mm)', 'FINISH / FILL', 'QUANTITY (NOS.)')]
    values = [
        item.material.name if item.material_id and item.material else '—',
        item.thickness.name if item.thickness_id and item.thickness else '—',
        item.size or '—',
        item.finish.name if item.finish_id and item.finish else '—',
        str(item.quantity or 0),
    ]
    spec = Table([head, [Paragraph(f'<b>{_txt(v)}</b>', S_BOLD) for v in values]],
                 colWidths=[width * 0.2] * 5)
    spec.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, LINE),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    top = [title, Spacer(1, 1 * mm)]
    if item.artwork_ref:
        top += [Paragraph(f'<i>Artwork Ref: {_txt(item.artwork_ref)}</i>', S_ART),
                Spacer(1, 3 * mm)]
    else:
        top.append(Spacer(1, 3 * mm))
    top.append(spec)
    elements.append(KeepTogether(top))
    elements.append(Spacer(1, 4 * mm))

    # ---- the price ladder, every slab, with the applicable one flagged ----
    tiers = list(item.tiers.all())
    if tiers:
        applicable = item.applicable_tier
        applicable_pk = applicable.pk if applicable else None
        rows = [[Paragraph(h, S_HEAD_CELL) for h in (
            'BATCH QUANTITY (NOS.)', 'RATE (PER UNIT)', 'EST. AMOUNT', '')]]
        applied_rows = []
        for index, tier in enumerate(tiers, start=1):
            applies = tier.pk is not None and tier.pk == applicable_pk
            if applies:
                applied_rows.append(index)
            rows.append([
                Paragraph(_tier_range(tier), S_CENTER),
                Paragraph(_rs(tier.rate_per_unit), S_CENTER),
                Paragraph(_rs(tier.estimated_amount), S_CENTER),
                Paragraph('APPLICABLE' if applies else '', S_CENTER_APPLIES),
            ])
        pricing = Table(rows, colWidths=[width * 0.30, width * 0.24, width * 0.26, width * 0.20])
        style = [
            ('BACKGROUND', (0, 0), (-1, 0), NAVY),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('BOX', (0, 0), (-1, -1), 0.75, LINE),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, LINE),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        for row_index in applied_rows:
            style.append(('BACKGROUND', (0, row_index), (-1, row_index), GREEN_BG))
        pricing.setStyle(TableStyle(style))
        elements.append(pricing)

        note = ('Rate applicable is based on the total ordered quantity for this item '
                '(slab pricing).')
        if applicable is None and item.quantity:
            note = ('No slab on this ladder covers the quantity of '
                    f'{item.quantity} Nos. &#8212; the rate is to be confirmed.')
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(Paragraph(note, S_NOTE))
    else:
        elements.append(Paragraph(
            '<i>No price tiers were entered for this line &#8212; the rate is nil.</i>',
            S_NOTE))

    if _dec(item.tooling_charge):
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(Paragraph(
            f'Tooling / screen charges for this item: <b>{_rs(item.tooling_charge)}</b> '
            f'(one-time, not included in the unit rate above).', S_NOTE))

    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        f'Line amount at {item.quantity or 0} Nos.: <b>{_rs(item.line_amount)}</b>',
        S_RIGHT_BOLD))
    elements.append(Spacer(1, 6 * mm))
    return elements


def _totals(quotation, width):
    gst_percent = _dec(quotation.gst_percent).normalize()
    rows = [['Sub Total (all line items)', _rs(quotation.sub_total)]]
    if _dec(quotation.tooling_charges):
        rows.append(['Tooling / Screen Charges (one-time)', _rs(quotation.tooling_charges)])
    if _dec(quotation.packing_forwarding):
        rows.append(['Packing &amp; Forwarding', _rs(quotation.packing_forwarding)])
    if _dec(quotation.freight):
        rows.append(['Freight / Shipping Charges', _rs(quotation.freight)])
    rows.append(['Taxable Amount', _rs(quotation.taxable_amount)])
    rows.append([f'GST @ {gst_percent}%', _rs(quotation.gst_amount)])

    body = [[Paragraph(label, S_NORMAL), Paragraph(f'<b>{value}</b>', S_RIGHT_BOLD)]
            for label, value in rows]
    body.append([Paragraph('<b>GRAND TOTAL</b>', S_TOTAL_L),
                 Paragraph(f'<b>{_rs(quotation.grand_total)}</b>', S_TOTAL_V)])

    table = Table(body, colWidths=[width * 0.70, width * 0.30])
    table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, LINE),
        ('INNERGRID', (0, 0), (-1, -2), 0.5, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 1.2, NAVY),
        ('TOPPADDING', (0, -1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
    ]))
    return table


def _footer(quotation, width):
    elements = [Paragraph(
        '<i>This is a system-generated quotation and does not require a physical '
        'signature.</i>', S_NOTE), Spacer(1, 8 * mm)]
    signature = Table([
        ['', Paragraph(f'For <b>{_txt(COMPANY_NAME)}</b>', S_BOLD)],
        ['', Spacer(1, 15 * mm)],
        ['', Paragraph('___________________________', S_NORMAL)],
        ['', Paragraph('Authorised Signatory', S_SMALL)],
    ], colWidths=[width * 0.55, width * 0.45])
    signature.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    elements.append(signature)
    return elements


def _page_furniture(quotation):
    """Foot every page with the quotation number and the page number."""
    label = f'{quotation.number} v{quotation.version}'

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(INK_SOFT)
        canvas.drawString(doc.leftMargin, 10 * mm, label)
        canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, f'Page {doc.page}')
        canvas.restoreState()
    return draw


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_quotation_pdf(quotation):
    """Render ``quotation`` and return the finished PDF as bytes.

    Safe on a half-built draft: no items, an item with no price tiers, a
    quantity of zero and blank customer fields are all rendered rather than
    raised.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=f'Quotation {quotation.number} v{quotation.version}',
        author=COMPANY_NAME,
    )
    width = A4[0] - 36 * mm
    story = [_header(quotation, width), Spacer(1, 6 * mm),
             _parties(quotation, width), Spacer(1, 5 * mm)]

    story.append(Paragraph('Dear Sir/Madam,', S_NORMAL))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        'We thank you for your enquiry and are pleased to submit our quotation as per the '
        'specifications and batch-wise pricing below. Unit rates vary by order quantity as '
        'shown in the pricing slab for each item.', S_NORMAL))
    story.append(Spacer(1, 6 * mm))

    items = list(quotation.items.all())
    if items:
        for item in items:
            story.extend(_item_block(item, width))
    else:
        story.append(Paragraph(
            '<i>No line items have been entered on this quotation yet.</i>', S_NOTE))
        story.append(Spacer(1, 6 * mm))

    story.append(Paragraph('<b>TOTALS</b>', S_SECTION))
    story.append(Spacer(1, 2 * mm))
    story.append(_totals(quotation, width))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph('<b>TERMS &amp; CONDITIONS</b>', S_SECTION))
    story.append(Spacer(1, 2 * mm))
    for label, text in TERMS:
        story.append(Paragraph(f'&#8226; <b>{label}:</b> {text}', S_TERM))
    story.append(Spacer(1, 10 * mm))

    story.extend(_footer(quotation, width))

    furniture = _page_furniture(quotation)
    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()


def quotation_pdf_filename(quotation):
    """'QTN-0425-v1.pdf' — the name the browser saves it under."""
    return f'{quotation.number}-v{quotation.version}.pdf'
