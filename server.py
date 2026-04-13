#!/usr/bin/env python3
"""
Generatore Proforma - Il Mannarino SRL
Produzione: gunicorn server:app

Variabili d'ambiente:
  DATA_DIR          → cartella per counter.json  (default: cartella script)
  N8N_SEARCH_URL    → webhook n8n per ricerca clienti BigQuery
  N8N_STORAGE_URL   → webhook n8n per salvataggio PDF su OneDrive

Contratto API n8n SEARCH (GET):
  Parametro:  ?q=testo
  Risposta:   [ { ragione_sociale, indirizzo, citta, partita_iva,
                  codice_univoco, codice_cliente, email_azienda }, ... ]
"""

import base64, io, json, os, datetime, threading
from flask import Flask, request, send_file, render_template_string, jsonify

app = Flask(__name__)

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.environ.get('DATA_DIR', BASE_DIR)
COUNTER_FILE = os.path.join(DATA_DIR, 'counter.json')
STORES_FILE  = os.path.join(BASE_DIR,  'stores.json')
_counter_lock = threading.Lock()


# ─── CODICI SDI ─────────────────────────────────────────────
SDI_CODES = [
    '0G6TBBX','0KDMVIB','10ZKECO','2LCMINU','2R4GTO8','38P86EY','3G3OPYL',
    '3RB98ZT','3ZJY534','4ADX8V9','596NUAX','5P3UNVR','5RUO82D','5W4A8J1',
    '66OZKW1','6EWHWLT','6JXPS2J','6RB0OU9','7035UR5','7HE8RN5','8CQGKGJ',
    'A4707H7','AO3AEUZ','AU7YEU4','B66HAMY','BA6ET11','BLY9JDQ','BY5KTZZ',
    'C1QQYZR','CEORGIG','DUDU0GE','DXEBYTP','E06UCUD','E2VWRNU','EH1R83N',
    'G1XGCBG','G4AI1U8','G7Q6SPJ','G9HZJRW','G9YK3BM','GR2P7ZP','H348Q01',
    'HHBD9AK','HQSIB42','I347Y6N','I6VXTJA','ISHDUAE','ITH9EQH','J6URRTW',
    'JC7P1DW','JHBM40P','K0ROACV','KBRM7PS','KGVVJ2H','KJSRCTG','KRRH6B9',
    'KUPCRMI','LX4UQQ5','M5ITOJA','M5UXCR1','M62SGNV','MJ1OYNU','MJEGRSK',
    'MRCC2DY','MZO2A0U','N3HJJJI','N92GLON','N9KM26R','NKNH5UQ','O8L2VB7',
    'OCCDHSV','P43TKPP','P4IUPYH','P62QHVQ','P83CKOC','PAXCCYU','PPX7BLB',
    'PUR1DAR','PXQYICS','PZIJH2V','QDZCM9N','QLDR2VY','QULXG4S','QYISEC3',
    'RGBDW7A','RN5Y3PI','RNMN7NC','ROINDUX','RTVLCR1','RWB54P8','RYRNP0U',
    'SA0PL6Q','SKUA8Y6','SN4CSRI','SNT102H','SU1UTOG','SU9YNJA','SUBM70N',
    'SZLUBAI','T04ZHR3','T9K4ZHO','TPICRCA','TRS3OH9','TRTSWMZ','TULURSB',
    'UE2LXTM','UNI0W8G','URSWIEX','USA39RA','USAL8PV','W4KYJ8V','W7YVJK9',
    'WH2KO8I','WHP7LTE','WNK4HCP','WP7SE2Q','WY7PJ6K','X2PH38J','X46AXNR',
    'XIT6IP5','XL13LG4','XMXAUP4','XWJKNZD','Y4BUAV4','YRXHCLN','ZCK6XHR',
]

# ─── STORES ─────────────────────────────────────────────────
def load_stores():
    try:
        with open(STORES_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


# ─── COUNTER (thread-safe) ──────────────────────────────────
def get_next_number():
    with _counter_lock:
        data = {'current': 35}
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                data = json.load(f)
        data['current'] += 1
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COUNTER_FILE, 'w') as f:
            json.dump(data, f)
        return data['current']

def peek_next_number():
    with _counter_lock:
        data = {'current': 35}
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                data = json.load(f)
        return data['current'] + 1


# ─── HELPERS ────────────────────────────────────────────────
def fmt_it(n):
    """Formatta numero in stile italiano: 1.234,56"""
    s = f'{abs(n):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('-' if n < 0 else '') + s

def fmt_date_dot(s):
    """Converte YYYY-MM-DD → DD.MM.YYYY"""
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d').strftime('%d.%m.%Y')
    except Exception:
        return s

def today_slash():
    return datetime.date.today().strftime('%d/%m/%Y')

def calc_importi(num_coperti, prezzo_persona):
    """
    Calcolo:  totale     = num_coperti × prezzo_persona  (prezzo IVA inclusa)
              imponibile = totale / 1.10
              iva        = totale - imponibile
    """
    totale     = round(int(num_coperti) * float(prezzo_persona), 2)
    imponibile = round(totale / 1.10, 2)
    iva        = round(totale - imponibile, 2)
    return imponibile, iva, totale


# ─── PDF GENERATION (ReportLab canvas) ──────────────────────
def generate_pdf(fd, numero):
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors

    buf = io.BytesIO()
    W, H = A4   # 595.28 x 841.89 pts
    c = rl_canvas.Canvas(buf, pagesize=A4)

    mL = 14 * mm
    mR = 14 * mm
    cW = W - mL - mR  # ~182mm

    num_coperti    = int(fd.get('num_coperti', 1))
    prezzo_persona = round(float(fd.get('prezzo_persona', 0)), 2)
    imp1, iva1, tot1 = calc_importi(num_coperti, prezzo_persona)

    num_coperti_2    = fd.get('num_coperti_2', '').strip()
    prezzo_persona_2 = fd.get('prezzo_persona_2', '').strip()
    has_row2 = bool(num_coperti_2 and prezzo_persona_2)
    if has_row2:
        num_coperti_2    = int(num_coperti_2)
        prezzo_persona_2 = round(float(prezzo_persona_2), 2)
        imp2, iva2, tot2 = calc_importi(num_coperti_2, prezzo_persona_2)
    else:
        imp2 = iva2 = tot2 = 0.0

    imponibile = round(imp1 + imp2, 2)
    iva        = round(iva1 + iva2, 2)
    totale     = round(tot1 + tot2, 2)

    acconto = round(float(fd.get('acconto', 0) or 0), 2)
    saldo   = round(totale - acconto, 2)

    today     = today_slash()
    data_cena = fmt_date_dot(fd.get('data_cena', ''))

    GRAY  = colors.Color(0.91, 0.91, 0.91)
    BLACK = colors.black

    # ── Helpers locali ──
    def txt(x, y, s, size=9, bold=False, italic=False, align='left', color=BLACK):
        c.setFillColor(color)
        font = 'Helvetica'
        if bold and italic: font = 'Helvetica-BoldOblique'
        elif bold:          font = 'Helvetica-Bold'
        elif italic:        font = 'Helvetica-Oblique'
        c.setFont(font, size)
        s = str(s)
        if align == 'center': c.drawCentredString(x, y, s)
        elif align == 'right': c.drawRightString(x, y, s)
        else: c.drawString(x, y, s)

    def hline(y, x1=None, x2=None, lw=0.5):
        c.setStrokeColor(BLACK)
        c.setLineWidth(lw)
        c.line(x1 if x1 is not None else mL,
               y,
               x2 if x2 is not None else (W - mR),
               y)

    def vline(x, y_top, y_bot, lw=0.3):
        c.setStrokeColor(BLACK)
        c.setLineWidth(lw)
        c.line(x, y_bot, x, y_top)

    def box(x, y, bw, bh, fill=None, lw=0.3):
        c.setStrokeColor(BLACK)
        c.setLineWidth(lw)
        if fill:
            c.setFillColor(fill)
            c.rect(x, y, bw, bh, fill=1, stroke=1)
            c.setFillColor(BLACK)
        else:
            c.rect(x, y, bw, bh, fill=0, stroke=1)

    # ── INIZIO LAYOUT ──
    y = H - 14 * mm

    # Intestazione azienda
    txt(mL, y, 'IL MANNARINO SRL', size=16, bold=True); y -= 6*mm
    txt(mL, y, 'VIALE GIACOMO MATTEOTTI 14/D', size=10); y -= 5*mm
    txt(mL, y, '20095 CUSANO MILANINO (MI)', size=10);  y -= 4.5*mm
    txt(mL, y, 'Codice fiscale 10747300969 - Partita IVA 10747300969', size=7.5); y -= 3.8*mm
    txt(mL, y, 'Iscritta presso il registro delle Imprese con il n\u00b0 10747300969', size=7.5); y -= 3.8*mm
    txt(mL, y, 'Capitale sociale: \u20ac 263.500,00 di cui \u20ac 263.500,00 i.v.', size=7.5); y -= 3.5*mm

    hline(y, lw=0.6); y -= 5*mm

    # ── NOTA PROFORMA + Data / Numero / Pagina ──
    bh_np = 17 * mm
    lw_np = cW * 0.65
    rw_np = cW - lw_np

    box(mL, y - bh_np, lw_np, bh_np)
    txt(mL + lw_np/2, y - bh_np/2 + 2.5*mm, 'NOTA PROFORMA',   size=13, bold=True, align='center')
    txt(mL + lw_np/2, y - bh_np/2 - 1*mm,
        'Il presente documento non ha rilevanza ai fini IVA',     size=6.5, italic=True, align='center')
    txt(mL + lw_np/2, y - bh_np/2 - 4.5*mm,
        'All\u2019atto del pagamento sar\u00e0 emessa regolare fattura', size=6.5, italic=True, align='center')


    rx   = mL + lw_np
    rrow = bh_np / 3
    for i, (lbl, val) in enumerate([('Data', today), ('Numero', str(numero)), ('Pagina', '1')]):
        ry = y - (i + 1) * rrow
        box(rx, ry, rw_np, rrow)
        txt(rx + 2*mm,        ry + rrow/2 - 1.2*mm, lbl, size=8, bold=True)
        txt(rx + rw_np * 0.5, ry + rrow/2 - 1.2*mm, val, size=9)

    y -= bh_np + 1*mm

    # ── DESTINATARIO ──
    # Altezza dinamica: 26mm se c'è email, 22mm altrimenti
    dest_h = 26 * mm if fd.get('email_azienda') else 22 * mm
    ld = cW * 0.46
    rd = cW - ld
    box(mL,      y - dest_h, ld, dest_h)
    box(mL + ld, y - dest_h, rd, dest_h)

    modifica_num = fd.get('modifica_proforma', '').strip()
    if modifica_num:
        txt(mL + ld/2, y - dest_h/2,
            f'Modifica della proforma N\u00b0 {modifica_num}', size=9, bold=True, align='center')

    dx = mL + ld + 2*mm
    txt(dx, y - 4*mm,    'Destinatario', size=7.5)
    txt(dx, y - 9*mm,    fd.get('ragione_sociale', ''), size=10, bold=True)
    txt(dx, y - 14*mm,   fd.get('indirizzo', ''), size=9)
    txt(dx, y - 18.5*mm, fd.get('citta', ''), size=9)
    if fd.get('email_azienda'):
        txt(dx, y - 23*mm, 'Email: ' + fd['email_azienda'], size=7.5)

    y -= dest_h

    # ── COD. CLIENTE / VALUTA / P.IVA / CODICE SDI / CF ──
    det_h   = 11 * mm
    det_cols = [35*mm, 13*mm, 42*mm, 38*mm, cW - 35*mm - 13*mm - 42*mm - 38*mm]
    det_data = [
        ('Cod. cliente',  fd.get('codice_cliente', '')),
        ('Valuta',        'EUR'),
        ('P.Iva',         fd.get('partita_iva', '')),
        ('Codice SDI',    fd.get('codice_sdi', '')),
        ('Codice fiscale', fd.get('partita_iva', '')),
    ]
    x = mL
    for cw, (lbl, val) in zip(det_cols, det_data):
        box(x, y - det_h, cw, det_h)
        txt(x + 1.5*mm, y - 4*mm, lbl, size=7.5, bold=True)
        txt(x + 1.5*mm, y - 9*mm, val, size=9)
        x += cw

    y -= det_h

    # ── RIGHE ARTICOLO ──
    # Colonne: Codice | Descrizione | UM | Q.tà | Prezzo unit. | Sconto | Importo | IVA
    li_cols = [
        ('Codice',       11*mm, 'left'),
        ('Descrizione',  71*mm, 'left'),
        ('UM',            8*mm, 'center'),
        ('Q.t\u00e0',   13*mm, 'right'),
        ('Prezzo unit.', 21*mm, 'right'),
        ('Sconto',       13*mm, 'right'),
        ('Importo',      20*mm, 'right'),
        ('IVA',          cW - 157*mm, 'right'),
    ]

    # Posizioni cumulative delle colonne
    col_x = [mL]
    for _, cw, _ in li_cols:
        col_x.append(col_x[-1] + cw)

    from reportlab.pdfbase.pdfmetrics import stringWidth
    hdr_h = 6 * mm

    desc      = f'Proforma per men\u00f9 del {data_cena}'
    store     = fd.get('nome_store', '')
    desc_maxw = li_cols[1][1] - 3*mm

    # Calcola se lo store fa wrap → altezza riga dinamica
    store_line1 = store_line2 = ''
    if store:
        if stringWidth(store, 'Helvetica', 8.5) <= desc_maxw:
            store_line1 = store
        else:
            mid = store.find('\u2013')
            if mid == -1:
                mid = store.rfind(' ', 0, len(store) // 2 + 15)
            if mid != -1:
                store_line1 = store[:mid].strip()
                store_line2 = store[mid+1:].strip()
            else:
                store_line1 = store

    if has_row2 and store_line2:
        row_h = 20 * mm
    elif has_row2 or store_line2:
        row_h = 16 * mm
    else:
        row_h = 12 * mm

    # Header grigio
    box(mL, y - hdr_h, cW, hdr_h, fill=GRAY)
    x = mL
    for lbl, cw, al in li_cols:
        if al == 'right':
            txt(x + cw - 1.5*mm, y - 4*mm, lbl, size=7.5, bold=True, align='right')
        elif al == 'center':
            txt(x + cw/2,        y - 4*mm, lbl, size=7.5, bold=True, align='center')
        else:
            txt(x + 1.5*mm,      y - 4*mm, lbl, size=7.5, bold=True)
        x += cw
    # Divisori verticali header
    for xp in col_x[1:-1]:
        vline(xp, y, y - hdr_h)
    y -= hdr_h

    # Riga dati
    box(mL, y - row_h, cW, row_h)
    for xp in col_x[1:-1]:
        vline(xp, y, y - row_h)

    if has_row2:
        # Layout fascia 2: store prima, poi data, poi numeri allineati per fascia
        if store_line1:
            txt(col_x[1] + 1.5*mm, y - 4.5*mm, store_line1, size=8.5)
        if store_line2:
            txt(col_x[1] + 1.5*mm, y - 9*mm,    store_line2, size=8.5)
            txt(col_x[1] + 1.5*mm, y - 13.5*mm, desc,        size=8.5)
            r1_y = y - 13.5*mm
            r2_y = y - 18*mm
        else:
            txt(col_x[1] + 1.5*mm, y - 9*mm, desc, size=8.5)
            r1_y = y - 9*mm
            r2_y = y - 13.5*mm
    else:
        txt(col_x[1] + 1.5*mm, y - 4.5*mm, desc, size=8.5)
        if store_line1:
            txt(col_x[1] + 1.5*mm, y - 9*mm, store_line1, size=8.5)
        if store_line2:
            txt(col_x[1] + 1.5*mm, y - 13*mm, store_line2, size=8.5)
        r1_y = y - 5*mm
        r2_y = None

    txt(col_x[3] + li_cols[3][1] - 1.5*mm, r1_y, str(num_coperti),       size=9, align='right')
    txt(col_x[4] + li_cols[4][1] - 1.5*mm, r1_y, fmt_it(prezzo_persona), size=9, align='right')
    txt(col_x[6] + li_cols[6][1] - 1.5*mm, r1_y, fmt_it(imp1),           size=9, align='right')
    txt(col_x[7] + 1.5*mm,                 r1_y, 'I10',                  size=9)

    if has_row2:
        txt(col_x[3] + li_cols[3][1] - 1.5*mm, r2_y, str(num_coperti_2),       size=9, align='right')
        txt(col_x[4] + li_cols[4][1] - 1.5*mm, r2_y, fmt_it(prezzo_persona_2), size=9, align='right')
        txt(col_x[6] + li_cols[6][1] - 1.5*mm, r2_y, fmt_it(imp2),             size=9, align='right')
        txt(col_x[7] + 1.5*mm,                 r2_y, 'I10',                    size=9)

    y -= row_h

    # ── SPAZIO VUOTO (righe aggiuntive future) ──
    footer_top = 115 * mm
    empty_h    = y - footer_top
    if empty_h > 2*mm:
        box(mL, footer_top, cW, empty_h)
    y = footer_top

    # ── PAGAMENTO ──
    pay_ratios = [0.28, 0.47, 0.25]
    pay_h = 20 * mm
    x = mL
    for r in pay_ratios:
        box(x, y - pay_h, cW * r, pay_h)
        x += cW * r

    txt(mL + 1.5*mm, y - 4*mm,    'Cond. di pagamento', size=7.5, bold=True)
    txt(mL + 1.5*mm, y - 8.5*mm,  'Bonifico bancario Rimessa diretta', size=8.5)

    bx = mL + cW * pay_ratios[0] + 1.5*mm
    txt(bx, y - 4*mm,    'Banca d\u2019appoggio', size=7.5, bold=True)
    txt(bx, y - 8.5*mm,  'UNICREDIT SPA', size=7.5)

    txt(mL + cW*(pay_ratios[0]+pay_ratios[1]) + 1.5*mm, y - 4*mm, 'Banca domiciliataria', size=7.5, bold=True)

    y -= pay_h

    # IBAN
    iban_h     = 5.5 * mm
    iban_split = cW * 0.72
    box(mL,               y - iban_h, iban_split,      iban_h)
    box(mL + iban_split,  y - iban_h, cW - iban_split, iban_h)
    txt(mL + 1.5*mm,            y - 3.8*mm, 'IBAN: IT79I0200820400000107322311', size=8.5, bold=True)
    txt(mL + iban_split + 1.5*mm, y - 3.8*mm, 'SWIFT: UNCRITM1350', size=8.5)
    y -= iban_h

    # Scadenze header
    sc_hdr_h = 5*mm
    box(mL, y - sc_hdr_h, cW, sc_hdr_h, fill=GRAY)
    txt(mL + cW/2, y - 3.5*mm, 'Scadenze', size=8.5, bold=True, align='center')
    y -= sc_hdr_h

    sc_row_h = 5.5 * mm
    sc_cols  = [0.28, 0.48, 0.24]

    sc_righe = [(today, 'Acconto', acconto), (data_cena, 'Saldo', saldo)] \
               if acconto > 0 else \
               [(data_cena, 'Bonifico bancario', totale)]

    for sc_data, sc_desc, sc_imp in sc_righe:
        x = mL
        for r in sc_cols:
            box(x, y - sc_row_h, cW * r, sc_row_h)
            x += cW * r
        txt(mL + 1.5*mm, y - 3.8*mm, sc_data, size=8.5)
        txt(mL + cW * sc_cols[0] + 1.5*mm, y - 3.8*mm, sc_desc, size=8.5)
        txt(mL + cW * (sc_cols[0]+sc_cols[1]) + cW*sc_cols[2] - 1.5*mm,
            y - 3.8*mm, fmt_it(sc_imp), size=8.5, align='right')
        y -= sc_row_h

    # ── IVA ──
    iva_hdr_h = 5*mm
    iva_row_h = 5.5*mm
    iva_cw = [20*mm, 0, 26*mm, 20*mm, 26*mm]
    iva_cw[1] = cW - sum(iva_cw[i] for i in [0,2,3,4])

    box(mL, y - iva_hdr_h, cW, iva_hdr_h, fill=GRAY)
    x = mL
    for i, (lbl, cw) in enumerate(zip(
            ['Codice IVA','Descrizione','Imponibile','Aliquota','Imposta'], iva_cw)):
        align = 'right' if i >= 2 else 'left'
        ox = x + cw - 1.5*mm if align == 'right' else x + 1.5*mm
        txt(ox, y - 3.5*mm, lbl, size=7.5, bold=True, align=align)
        x += cw
    y -= iva_hdr_h

    x = mL
    for i, (val, cw) in enumerate(zip(
            ['I10', 'IVA 10%', fmt_it(imponibile), '10,00%', fmt_it(iva)], iva_cw)):
        box(x, y - iva_row_h, cw, iva_row_h)
        align = 'right' if i >= 2 else 'left'
        ox = x + cw - 1.5*mm if align == 'right' else x + 1.5*mm
        txt(ox, y - 3.8*mm, val, size=8.5, align=align)
        x += cw
    y -= iva_row_h

    # ── DETTAGLIO IMPORTI ──
    y -= 2*mm
    det_hdr_h = 5*mm
    box(mL, y - det_hdr_h, cW, det_hdr_h, fill=GRAY)
    txt(mL + cW/2, y - 3.5*mm, 'Dettaglio importi', size=8.5, bold=True, align='center')
    y -= det_hdr_h

    tot_rh = 5.2 * mm
    tc = [0]*6
    tc[0] = cW * 0.195
    tc[1] = cW * 0.105
    tc[2] = cW * 0.195
    tc[3] = cW * 0.105
    tc[4] = cW * 0.25
    tc[5] = cW - tc[0]-tc[1]-tc[2]-tc[3]-tc[4]

    tot_rows = [
        ('Spese di incasso','0,00','Bolli','0,00','Merci e servizi', fmt_it(imponibile)),
        ('Totale imposta', fmt_it(iva), '','','Totale imponibile', fmt_it(imponibile)),
        ('Spese anticipate','0,00','Altre spese','0,00','Spese di trasporto','0,00'),
        ('','','','','Totale documento', fmt_it(totale)),
        ('N\u00b0 colli','0','Peso Kg','0,00','Sconti','0,00'),
        ('Acconti', fmt_it(acconto) if acconto > 0 else '0,00', 'Ritenuta','','Totale da pagare', fmt_it(saldo if acconto > 0 else totale)),
    ]

    for row_data in tot_rows:
        x = mL
        for i, val in enumerate(row_data):
            cw = tc[i]
            box(x, y - tot_rh, cw, tot_rh)
            if val:
                is_label = (i % 2 == 0)
                is_total = (i == 5 and row_data[4] in ('Totale documento','Totale da pagare'))
                bold_val = is_label or is_total
                align    = 'left' if is_label else 'right'
                ox = x + 1.5*mm if align == 'left' else x + cw - 1.5*mm
                txt(ox, y - 3.5*mm, val, size=7.5, bold=bold_val, align=align)
            x += cw
        y -= tot_rh

    c.save()
    buf.seek(0)
    return buf



# ─── HTML FORM ───────────────────────────────────────────────
HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proforma – Il Mannarino</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#111;min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:32px 16px}
.card{background:#fff;border-radius:16px;padding:40px 44px;max-width:640px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.logo-row{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.logo-icon{width:40px;height:40px;background:#111;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:20px}
.logo-text{font-size:18px;font-weight:800;letter-spacing:1.5px;color:#111}
.logo-sub{font-size:11px;color:#999;letter-spacing:1px;text-transform:uppercase;margin-bottom:24px}
.badge{display:inline-flex;align-items:center;gap:6px;background:#f8f2e8;border:1px solid #e8d8b0;color:#7a5f20;border-radius:8px;padding:10px 16px;font-size:13px;font-weight:500;margin-bottom:24px}
.badge strong{font-size:15px;color:#3a2800}
.sec{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#c09030;margin:24px 0 14px;padding-bottom:7px;border-bottom:1px solid #f0e8d0}
.field{margin-bottom:12px}
label{display:block;font-size:11px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:#666;margin-bottom:5px}
input,select{width:100%;border:1.5px solid #e0e0e0;border-radius:8px;padding:10px 14px;font-size:14px;color:#111;background:#fafafa;transition:all .18s;font-family:inherit}
input:focus,select:focus{outline:none;border-color:#c09030;background:#fff;box-shadow:0 0 0 3px rgba(192,144,48,.12)}
input.db-fill{background:#fdf8ee;border-color:#e8d5a0;color:#555}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.hint{font-size:11px;color:#aaa;margin-top:5px}

/* Autocomplete */
.search-wrap{position:relative}
.ac-list{position:absolute;top:calc(100% + 2px);left:0;right:0;background:#fff;border:1.5px solid #c09030;border-radius:0 0 10px 10px;z-index:200;max-height:240px;overflow-y:auto;display:none;box-shadow:0 8px 24px rgba(0,0,0,.15)}
.ac-list.open{display:block}
.ac-item{padding:10px 14px;cursor:pointer;border-bottom:1px solid #f0e8d0}
.ac-item:last-child{border-bottom:none}
.ac-item:hover{background:#fdf6e8}
.ac-item strong{display:block;font-size:13px;color:#111;margin-bottom:2px}
.ac-item span{font-size:11px;color:#888}
.ac-msg{padding:12px 14px;font-size:12px;color:#999;text-align:center}

/* Badge DB / Reset */
.db-banner{display:none;align-items:center;gap:10px;background:#eaf7ea;border:1px solid #a8d8a8;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-size:12px;color:#1a6a1a;font-weight:600}
.db-banner.show{display:flex}
.btn-reset-cl{background:none;border:none;color:#c09030;font-size:11px;cursor:pointer;text-decoration:underline;padding:0;margin-left:auto;font-family:inherit}

/* Calcolo preview */
.calc-box{background:#f8f2e8;border:1px solid #e8d8b0;border-radius:10px;padding:16px 20px;margin-top:8px}
.calc-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:13px}
.calc-row .lbl{color:#888}
.calc-row .val{font-size:13px;color:#333}
.calc-row.total-row{border-top:1px solid #e0c870;margin-top:8px;padding-top:10px}
.calc-row.total-row .lbl{font-size:14px;font-weight:700;color:#111}
.calc-row.total-row .val{font-size:16px;font-weight:800;color:#111}
.calc-row.bold-row .lbl{font-size:14px;font-weight:700;color:#111}
.calc-row.bold-row .val{font-size:16px;font-weight:800;color:#111}
.calc-row.dim-row .lbl{color:#ccc}
.calc-row.dim-row .val{color:#ccc}

/* Bottoni */
.btn-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:28px}
.btn{background:#111;color:#fff;border:none;border-radius:10px;padding:15px;font-size:14px;font-weight:700;cursor:pointer;letter-spacing:.5px;transition:all .18s;display:flex;align-items:center;justify-content:center;gap:7px;font-family:inherit}
.btn:hover{background:#333;transform:translateY(-1px);box-shadow:0 6px 20px rgba(0,0,0,.25)}
.btn:disabled{background:#999;cursor:not-allowed;transform:none;box-shadow:none}
.btn.pdf{background:#c0392b}.btn.pdf:hover{background:#e74c3c}
.ok{display:none;background:#e8f5e8;border:1px solid #a8d8a8;border-radius:8px;padding:13px 18px;color:#1a6a1a;font-size:14px;margin-bottom:16px;font-weight:500}
.ok.show{display:flex;align-items:center;gap:8px}
</style>
</head>
<body>
<div class="card">

  <div class="logo-row">
    <div class="logo-icon">&#127860;</div>
    <div><div class="logo-text">IL MANNARINO SRL</div></div>
  </div>
  <div class="logo-sub">Generatore Note Proforma</div>
  <div class="badge">&#128196; Prossima proforma: N&#176; <strong id="nn">{{ next_num }}</strong></div>
  <div class="ok" id="ok"></div>

  <form id="f">

    <!-- ── RICERCA CLIENTE ── -->
    <div class="sec">Ricerca Cliente</div>

    <div class="field search-wrap">
      <label>Cerca per Ragione Sociale</label>
      <input id="search_input" autocomplete="off"
             placeholder="es. BBV Gastaldi… (min. 2 caratteri)">
      <div class="ac-list" id="ac_list"></div>
      <div class="hint">Se il cliente non viene trovato, compila i campi manualmente sotto &#8595;</div>
    </div>

    <div class="db-banner" id="db_banner">
      <span>&#9989; Dati compilati dal database</span>
      <button type="button" class="btn-reset-cl" onclick="resetCliente()">&#9998; Modifica / Svuota</button>
    </div>

    <div class="field">
      <label>Ragione Sociale *</label>
      <input name="ragione_sociale" id="ragione_sociale" required placeholder="es. BBV Gastaldi Events S.r.l.">
    </div>
    <div class="field">
      <label>Indirizzo *</label>
      <input name="indirizzo" id="indirizzo" required placeholder="es. Piazza Luigi di Savoia 22">
    </div>
    <div class="field">
      <label>CAP, Citt&#224;, Provincia *</label>
      <input name="citta" id="citta" required placeholder="es. 20124 MILANO MI">
    </div>
    <div class="g2">
      <div class="field">
        <label>Partita IVA *</label>
        <input name="partita_iva" id="partita_iva" required placeholder="es. 05178360961">
      </div>
      <div class="field">
        <label>Codice Cliente</label>
        <input name="codice_cliente" id="codice_cliente" placeholder="Auto dal DB">
      </div>
    </div>
    <div class="field">
      <label>Email Azienda</label>
      <input type="email" name="email_azienda" id="email_azienda" placeholder="es. info@azienda.it">
    </div>
    <div class="field">
      <label>Codice SDI *</label>
      <select name="codice_sdi" id="codice_sdi" required>
        <option value="">Seleziona codice SDI&hellip;</option>
        {% for code in sdi_codes %}
        <option value="{{ code }}">{{ code }}</option>
        {% endfor %}
      </select>
    </div>

    <!-- ── MODIFICA PROFORMA ── -->
    <div class="sec">Modifica Proforma</div>
    <div class="field">
      <label>Sostituisce proforma N&#176;</label>
      <input type="number" name="modifica_proforma" id="modifica_proforma"
             min="1" step="1" placeholder="Lascia vuoto se &egrave; una nuova proforma">
      <div class="hint">Compilare solo se questa proforma sostituisce una precedente</div>
    </div>

    <!-- ── DETTAGLI EVENTO ── -->
    <div class="sec">Dettagli Evento</div>

    <div class="field">
      <label>Store *</label>
      <select name="nome_store" id="nome_store" required
              onchange="document.getElementById('store_email').value=this.options[this.selectedIndex].dataset.email||''">
        <option value="">Seleziona store&#8230;</option>
        {% for store in stores %}
        <option value="{{ store.name }}" data-email="{{ store.email }}">{{ store.name }}</option>
        {% endfor %}
      </select>
      <input type="hidden" name="store_email" id="store_email">
    </div>

    <div class="g2">
      <div class="field">
        <label>Data della Cena *</label>
        <input type="date" name="data_cena" required>
      </div>
      <div class="field">
        <label>N&#176; Coperti *</label>
        <input type="number" name="num_coperti" id="num_coperti"
               min="1" step="1" required placeholder="es. 10"
               oninput="aggiornaCalcolo()">
      </div>
    </div>

    <div class="field">
      <label>Prezzo per Persona &#8364; (IVA inclusa) *</label>
      <input type="number" name="prezzo_persona" id="prezzo_persona"
             step="0.01" min="0.01" required placeholder="es. 34.55"
             oninput="aggiornaCalcolo()">
    </div>

    <div class="g2">
      <div class="field">
        <label>N&#176; Coperti 2</label>
        <input type="number" name="num_coperti_2" id="num_coperti_2"
               min="1" step="1" placeholder="opzionale"
               oninput="aggiornaCalcolo()">
      </div>
      <div class="field">
        <label>Prezzo per Persona 2 &#8364; (IVA inclusa)</label>
        <input type="number" name="prezzo_persona_2" id="prezzo_persona_2"
               step="0.01" min="0.01" placeholder="opzionale"
               oninput="aggiornaCalcolo()">
      </div>
    </div>

    <div class="field">
      <label>Acconto &#8364; (opzionale)</label>
      <input type="number" name="acconto" id="acconto"
             step="0.01" min="0.01" placeholder="Lascia vuoto se non c&#39;&egrave; acconto"
             oninput="aggiornaCalcolo()">
    </div>

    <!-- ── PREVIEW CALCOLO ── -->
    <div class="calc-box">
      <div class="calc-row bold-row">
        <span class="lbl">Totale IVA inclusa</span>
        <span class="val" id="cp_tot">&#8364; 0,00</span>
      </div>
      <div class="calc-row dim-row">
        <span class="lbl">di cui Imponibile</span>
        <span class="val" id="cp_imp">&#8364; 0,00</span>
      </div>
      <div class="calc-row dim-row">
        <span class="lbl">di cui IVA 10%</span>
        <span class="val" id="cp_iva">&#8364; 0,00</span>
      </div>
      <div class="calc-row dim-row" id="row_acconto" style="display:none;border-top:1px solid #e0c870;margin-top:8px;padding-top:10px">
        <span class="lbl">Acconto</span>
        <span class="val" id="cp_acc">&#8364; 0,00</span>
      </div>
      <div class="calc-row total-row" id="row_saldo" style="display:none">
        <span class="lbl">Saldo da pagare</span>
        <span class="val" id="cp_sal">&#8364; 0,00</span>
      </div>
    </div>

    <div class="btn-row">
      <button type="button" class="btn pdf" onclick="genera()">&#128196; Scarica PDF</button>
    </div>

  </form>
</div>

<script>
// ── Autocomplete ──────────────────────────────────────────
var _acTimer  = null;
var _acData   = [];

document.getElementById('search_input').addEventListener('input', function(){
  var val = this.value.trim();
  clearTimeout(_acTimer);
  var list = document.getElementById('ac_list');
  if(val.length < 2){ list.innerHTML=''; list.classList.remove('open'); return; }
  list.innerHTML = '<div class="ac-msg">&#128269; Ricerca in corso&#8230;</div>';
  list.classList.add('open');
  _acTimer = setTimeout(function(){ searchCliente(val); }, 400);
});

async function searchCliente(q){
  var list = document.getElementById('ac_list');
  try{
    var r = await fetch('/clienti?q=' + encodeURIComponent(q));
    var data = await r.json();
    _acData = data;
    if(data.length === 0){
      list.innerHTML = '<div class="ac-msg">Nessun cliente trovato &mdash; compila manualmente</div>';
      return;
    }
    list.innerHTML = data.map(function(cl, i){
      return '<div class="ac-item" onclick="selezionaCliente(' + i + ')">' +
             '<strong>' + esc(cl.ragione_sociale) + '</strong>' +
             '<span>' + esc(cl.indirizzo || '') + ' &nbsp;&bull;&nbsp; P.IVA ' + esc(cl.partita_iva || '') + '</span>' +
             '</div>';
    }).join('');
  }catch(e){
    list.innerHTML = '<div class="ac-msg">Errore connessione &mdash; compila manualmente</div>';
  }
}

function selezionaCliente(idx){
  var cl = _acData[idx];
  var campi = ['ragione_sociale','indirizzo','citta','partita_iva','codice_sdi','codice_cliente','email_azienda'];
  campi.forEach(function(f){
    var el = document.getElementById(f);
    if(el){ el.value = cl[f] || ''; el.classList.add('db-fill'); }
  });
  document.getElementById('search_input').value = cl.ragione_sociale;
  document.getElementById('ac_list').classList.remove('open');
  document.getElementById('db_banner').classList.add('show');
}

function resetCliente(){
  var campi = ['ragione_sociale','indirizzo','citta','partita_iva','codice_sdi','codice_cliente','email_azienda'];
  campi.forEach(function(f){
    var el = document.getElementById(f);
    if(el){ el.value = ''; el.classList.remove('db-fill'); }
  });
  document.getElementById('search_input').value = '';
  document.getElementById('db_banner').classList.remove('show');
}

document.addEventListener('click', function(e){
  if(!e.target.closest('.search-wrap')){
    document.getElementById('ac_list').classList.remove('open');
  }
});

function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Calcolo live ─────────────────────────────────────────
function fmtEu(n){
  return n.toLocaleString('it-IT', {minimumFractionDigits:2, maximumFractionDigits:2});
}

function aggiornaCalcolo(){
  var cov = parseInt(document.getElementById('num_coperti').value)     || 0;
  var pr  = parseFloat(document.getElementById('prezzo_persona').value) || 0;
  var cov2 = parseInt(document.getElementById('num_coperti_2').value)   || 0;
  var pr2  = parseFloat(document.getElementById('prezzo_persona_2').value) || 0;
  var acc = parseFloat(document.getElementById('acconto').value)        || 0;
  var tot1 = Math.round(cov * pr * 100) / 100;
  var tot2 = Math.round(cov2 * pr2 * 100) / 100;
  var tot  = Math.round((tot1 + tot2) * 100) / 100;
  var imp = Math.round(tot / 1.10 * 100) / 100;
  var iva = Math.round((tot - imp) * 100) / 100;
  var sal = Math.round((tot - acc) * 100) / 100;
  document.getElementById('cp_tot').textContent = '\u20ac ' + fmtEu(tot);
  document.getElementById('cp_imp').textContent = '\u20ac ' + fmtEu(imp);
  document.getElementById('cp_iva').textContent = '\u20ac ' + fmtEu(iva);
  var showAcc = acc > 0;
  document.getElementById('row_acconto').style.display = showAcc ? 'flex' : 'none';
  document.getElementById('row_saldo').style.display   = showAcc ? 'flex' : 'none';
  document.getElementById('cp_acc').textContent = '\u20ac ' + fmtEu(acc);
  document.getElementById('cp_sal').textContent = '\u20ac ' + fmtEu(sal);
}

// ── Genera documento ─────────────────────────────────────
async function genera(){
  var f = document.getElementById('f');
  if(!f.checkValidity()){ f.reportValidity(); return; }
  var btns = document.querySelectorAll('.btn');
  btns.forEach(function(b){ b.disabled=true; });
  try{
    var fd = new FormData(f);
    var r  = await fetch('/genera', {method:'POST', body:fd});
    if(!r.ok){ var t=await r.text(); alert('Errore: '+t); return; }
    var blob = await r.blob();
    var cd   = r.headers.get('Content-Disposition') || '';
    var m    = cd.match(/filename="(.+?)"/);
    var fn   = m ? m[1] : 'proforma.pdf';
    var a    = document.createElement('a');
    a.href   = URL.createObjectURL(blob);
    a.download = fn; a.click();
    var num = parseInt(document.getElementById('nn').textContent) + 1;
    document.getElementById('nn').textContent = num;
    var ok = document.getElementById('ok');
    ok.textContent = '\u2705 Proforma N\u00b0 ' + (num-1) + ' generata con successo!';
    ok.classList.add('show');
    setTimeout(function(){ ok.classList.remove('show'); }, 5000);
    f.reset(); resetCliente(); aggiornaCalcolo();
  }finally{
    btns.forEach(function(b){ b.disabled=false; });
  }
}
</script>
</body>
</html>'''


# ─── ROUTES ─────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(HTML,
                                  next_num=peek_next_number(),
                                  stores=load_stores(),
                                  sdi_codes=SDI_CODES)


@app.route('/clienti')
def search_clienti():
    """
    Autocomplete clienti — chiama il webhook n8n che interroga BigQuery.
    Configura N8N_SEARCH_URL come variabile d'ambiente.
    Risposta attesa da n8n: lista di oggetti con campi:
      ragione_sociale, indirizzo, citta, partita_iva,
      codice_univoco, codice_cliente, email_azienda
    """
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    n8n_url = os.environ.get('N8N_SEARCH_URL', '')
    if not n8n_url:
        # N8N non ancora configurato → ritorna lista vuota (no errore)
        return jsonify([])

    try:
        import requests as req
        resp = req.get(n8n_url, params={'q': q}, timeout=5)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        app.logger.error(f'[/clienti] errore chiamata n8n: {e}')
        return jsonify([])


@app.route('/genera', methods=['POST'])
def genera():
    fd     = request.form.to_dict()
    numero = get_next_number()

    safe     = ''.join(c for c in fd.get('ragione_sociale','')
                       if c.isalnum() or c in ' _-')[:30].strip().replace(' ', '_')
    filename = f'Proforma_{str(numero).zfill(4)}_{safe}.pdf'

    buf      = generate_pdf(fd, numero)
    mimetype = 'application/pdf'

    # ── Notifica asincrona a n8n per storage ──
    n8n_storage_url = os.environ.get('N8N_STORAGE_URL', '')
    if n8n_storage_url:
        pdf_bytes = buf.getvalue()

        def _notify():
            try:
                import requests as req
                req.post(n8n_storage_url, json={
                    'filename':        filename,
                    'numero':          numero,
                    'ragione_sociale': fd.get('ragione_sociale', ''),
                    'indirizzo':       fd.get('indirizzo', ''),
                    'citta':           fd.get('citta', ''),
                    'partita_iva':     fd.get('partita_iva', ''),
                    'codice_sdi':      fd.get('codice_sdi', ''),
                    'codice_cliente':  fd.get('codice_cliente', ''),
                    'email_azienda':   fd.get('email_azienda', ''),
                    'nome_store':      fd.get('nome_store', ''),
                    'store_email':     fd.get('store_email', ''),
                    'data':            datetime.date.today().isoformat(),
                    'pdf_base64':      base64.b64encode(pdf_bytes).decode(),
                }, timeout=15)
                app.logger.info(f'[n8n storage] PDF {filename} inviato')
            except Exception as e:
                app.logger.error(f'[n8n storage] errore: {e}')

        threading.Thread(target=_notify, daemon=True).start()
        buf = io.BytesIO(pdf_bytes)   # ricrea buffer dopo getvalue()

    return send_file(buf, mimetype=mimetype,
                     as_attachment=True, download_name=filename)


if __name__ == '__main__':
    print('\n\U0001f37d  Generatore Proforma \u2013 Il Mannarino SRL')
    print('   Apri nel browser: http://localhost:5050\n')
    app.run(host='0.0.0.0', port=5050, debug=False)
