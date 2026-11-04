import os
import re
import tempfile
import urllib.request
from io import BytesIO
from collections import defaultdict
import streamlit as st
import pdfplumber
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
from pdf2image import convert_from_path
from pyzbar.pyzbar import decode
from PIL import Image

try:
    import pytesseract
except ImportError:
    pytesseract = None

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak, Paragraph, Spacer, Image as RLImage
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.barcode import code128
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ==========================================
# 1. ARAYÜZ VE TÜRKÇE FONT AYARLARI
# ==========================================
st.set_page_config(page_title="Trendyol Etiket Düzenleyici", page_icon="📦", layout="wide")
st.title("📦 Trendyol Toplu Etiket")

@st.cache_resource
def load_turkish_fonts():
    font_path = "Roboto-Regular.ttf"
    bold_font_path = "Roboto-Bold.ttf"
    
    if not os.path.exists(font_path):
        urllib.request.urlretrieve("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf", font_path)
    if not os.path.exists(bold_font_path):
        urllib.request.urlretrieve("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Bold.ttf", bold_font_path)
        
    pdfmetrics.registerFont(TTFont('Roboto', font_path))
    pdfmetrics.registerFont(TTFont('Roboto-Bold', bold_font_path))
    return 'Roboto', 'Roboto-Bold'

MAIN_FONT, BOLD_FONT = load_turkish_fonts()

def fix_tr(text):
    if not isinstance(text, str): return str(text)
    return text.replace('\n', ' ').strip()

# ==========================================
# 2. KANUN KURAL TABANLI PARSER (RULE-BASED)
# ==========================================
def parse_trendyol_attribute_block(text_block, cargo_bc="N/A"):
    """
    Kural 1: Adet kanunu -> RAKAM + "ADET" (Arada boşluk var)
    Kural 2: Beden -> Tek Ebat / Tek Beden / Standart / XL / Numara
    Kural 3: Renk -> RENKLİ / BEYAZ / SİYAH / Renk adı
    Kural 4: Birleşik Barkod + Stok Kodu Bölücü (TYB... + Slug)
    """
    # 1. ADET KANUNU (RAKAM + ADET)
    qty_match = re.search(r'\b(\d+)\s*(?:Adet|ADET|adet)\b', text_block)
    qty = f"{qty_match.group(1)} Adet" if qty_match else "1 Adet"

    # 2. ÜRÜN SIRALAMASI (1, 2, 3...)
    seq_match = re.search(r'(?:^|\n|\s)(\d{1,2})\s+(?:1|Adet|Renk|Beden|\n)', text_block)
    seq = seq_match.group(1) if seq_match else "1"

    # 3. BEDEN TESPİTİ VE FALLBACK
    size_pattern = r'\b(Tek\s*Ebat|Tek\s*Beden|Standart|One\s*Size|XS|S|M|L|XL|[2-4]XL|\d{2,3})\b'
    size_match = re.search(size_pattern, text_block, re.IGNORECASE)
    size = size_match.group(1).title() if size_match else "Tek Ebat"

    # 4. RENK TESPİTİ VE FALLBACK
    color_pattern = r'\b(RENKLİ|RENKLI|BEYAZ|SİYAH|SIYAH|GRİ|GRI|KIRMIZI|MAVİ|MAVI|SARI|YEŞİL|YESIL|PEMBE|TURUNCU|LACİVERT|LACIVERT|KAHVERENGİ|GÜMÜŞ|ALTIN|Tek\s*Renk)\b'
    color_match = re.search(color_pattern, text_block, re.IGNORECASE)
    color = color_match.group(1).upper() if color_match else "-"

    # 5. BARKOD VE STOK KODU AYRIŞTIRICI (BİRLEŞİK METİN ÇÖZÜCÜ)
    barcode = "-"
    sku = "-"

    combined_match = re.search(r'\b(TYB[A-Z0-9]{12,20})([a-zA-Z0-9_\-\u00c0-\u017f]+)?\b', text_block)
    
    if combined_match:
        barcode = combined_match.group(1)
        sku = combined_match.group(2) if combined_match.group(2) else "-"
    else:
        bc_match = re.search(r'\b(\d{6,14})\b', text_block)
        if bc_match and bc_match.group(1) not in qty and bc_match.group(1) != cargo_bc:
            barcode = bc_match.group(1)

        sku_match = re.search(r'Stok Kodu\s*[:\n\|]?\s*([a-zA-Z0-9_\-\u00c0-\u017f]+)', text_block, re.IGNORECASE)
        if sku_match and sku_match.group(1).lower() not in ['ptt', 'barkod', 'beden', 'renk']:
            sku = sku_match.group(1)

    return {
        "sequence": seq,
        "qty": qty,
        "color": color,
        "size": size,
        "barcode": barcode,
        "sku": sku
    }

# ==========================================
# 3. UZAMSAL (SPATIAL) HASSAS VERİ ÇEKME
# ==========================================
def extract_order_data_from_pdf(pdf_path):
    orders_on_page = []
    raw_texts = [] 
    
    try:
        images = convert_from_path(pdf_path)
    except Exception:
        images = []

    for page_idx, page_layout in enumerate(extract_pages(pdf_path)):
        page_img = images[page_idx] if page_idx < len(images) else None

        # 1. Kargo Logosunu Büyük ve Okunabilir Boyutta Kırpma
        cargo_company = "PTT Kargo"
        cargo_logo_bytes = None
        cropped_img_pil = None
        
        if page_img:
            w, h = page_img.size
            crop_box = (int(w * 0.65), int(h * 0.02), int(w * 0.99), int(h * 0.22))
            cropped_img_pil = page_img.crop(crop_box)
            
            buf = BytesIO()
            cropped_img_pil.save(buf, format='PNG')
            cargo_logo_bytes = buf.getvalue()

        # 2. Metin Bloklarını Koordinatlı Çekme
        text_blocks = []
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                text_blocks.append({
                    'bbox': element.bbox,
                    'text': element.get_text().strip()
                })
                
        full_page_text = "\n".join([b['text'] for b in text_blocks])
        text_upper = full_page_text.upper()

        # Kargo Firması Tespiti (PTT Öncelikli)
        if "PTT" in text_upper: cargo_company = "PTT Kargo"
        elif "YURTICI" in text_upper or "YURTİÇİ" in text_upper: cargo_company = "Yurtiçi Kargo"
        elif "ARAS" in text_upper: cargo_company = "Aras Kargo"
        elif "MNG" in text_upper: cargo_company = "MNG Kargo"
        elif "SURAT" in text_upper or "SÜRAT" in text_upper: cargo_company = "Sürat Kargo"
        elif "TRENDYOL EXPRESS" in text_upper or " TEX " in text_upper: cargo_company = "Trendyol Express"
        elif "SENDEO" in text_upper: cargo_company = "Sendeo"
        elif "HEPSIJET" in text_upper: cargo_company = "HepsiJET"

        raw_texts.append({
            "page": page_idx + 1, 
            "text": full_page_text, 
            "cropped_img": cropped_img_pil,
            "detected_cargo": cargo_company
        })

        # 3. Sipariş No
        order_no = "N/A"
        m_ord = re.search(r'Sipariş No\s*[:\|]?\s*(\d+)', full_page_text)
        if m_ord: order_no = m_ord.group(1).strip()

        # 4. Alıcı Adı
        recipient = "N/A"
        for b in text_blocks:
            if "Ad-Soyad" in b['text']:
                y_center = (b['bbox'][1] + b['bbox'][3]) / 2
                for b2 in text_blocks:
                    if abs((b2['bbox'][1] + b2['bbox'][3])/2 - y_center) < 15 and b2['bbox'][0] > 95:
                        r_candidate = b2['text'].replace('\n', ' ').strip()
                        if r_candidate.startswith(":"): r_candidate = r_candidate[1:].strip()
                        if r_candidate: recipient = fix_tr(r_candidate)

        # 5. Adres
        address = "N/A"
        for b in text_blocks:
            if "Adres" in b['text'] or b['text'].startswith("Adres"):
                y_top = b['bbox'][3] + 15
                y_bottom = b['bbox'][1] - 120
                addr_parts = []
                for b2 in text_blocks:
                    if b2['bbox'][0] > 95 and y_bottom <= b2['bbox'][1] <= y_top and b2['bbox'][2] < 320:
                        t = b2['text'].strip()
                        if t != recipient and t != f": {recipient}" and "Sipariş No" not in t and "7340" not in t and "Ad-Soyad" not in t:
                            if t.startswith(":"):
                                t_sub = t[1:].strip()
                                if t_sub and t_sub != recipient: addr_parts.append(t_sub.replace('\n', ' '))
                            else:
                                addr_parts.append(t.replace('\n', ' '))
                if addr_parts: address = fix_tr(" ".join(addr_parts))

        # 6. Kargo Barkodu
        cargo_bc = "N/A"
        m_bc = re.search(r'Kargo Barkodu\s*\n?\s*(\d{13,16})', full_page_text)
        if not m_bc: m_bc = re.search(r'\b(734\d{13})\b', full_page_text)
        if m_bc: cargo_bc = m_bc.group(1).strip()
        
        if cargo_bc == "N/A" and page_img:
            try:
                decoded = decode(page_img)
                if decoded: cargo_bc = decoded[0].data.decode('utf-8')
            except: pass

        # 7. KURAL TABANLI ÜRÜN VE ADET PARÇALAMA
        products = []
        for b in text_blocks:
            t = b['text']
            if "Adet" in t:
                lines = [l.strip() for l in t.split('\n') if l.strip()]
                if "Adet" in lines:
                    adet_idx = lines.index("Adet")
                    name_part = " ".join(lines[:adet_idx]).strip()
                    
                    attr_block_text = "\n".join(lines[adet_idx:])
                    
                    p_y_bottom = b['bbox'][1] - 40
                    p_y_top = b['bbox'][3] + 40
                    for b2 in text_blocks:
                        if p_y_bottom <= b2['bbox'][1] <= p_y_top or p_y_bottom <= b2['bbox'][3] <= p_y_top:
                            attr_block_text += "\n" + b2['text']

                    parsed_attr = parse_trendyol_attribute_block(attr_block_text, cargo_bc)
                    
                    if name_part:
                        products.append({
                            'index': parsed_attr["sequence"],
                            'name': fix_tr(name_part),
                            'qty': parsed_attr["qty"],
                            'color': parsed_attr["color"],
                            'size': parsed_attr["size"],
                            'barcode': parsed_attr["barcode"],
                            'sku': parsed_attr["sku"]
                        })

        if not products:
            products.append({
                'index': '1',
                'name': recipient + ' Siparişi',
                'qty': '1 Adet',
                'color': '-',
                'size': 'Tek Ebat',
                'barcode': cargo_bc,
                'sku': '-'
            })

        total_qty = sum([int(re.search(r'\d+', p["qty"]).group(0)) for p in products if re.search(r'\d+', p["qty"])])
        is_multi = len(products) > 1 or total_qty > 1
        
        orders_on_page.append({
            "order_no": order_no, 
            "recipient": recipient, 
            "address": address,
            "cargo_barcode": cargo_bc, 
            "cargo_company": cargo_company, 
            "cargo_logo_bytes": cargo_logo_bytes,
            "products": products, 
            "is_multi": is_multi, 
            "group_key": products[0]["name"] if products else "Diger_Urunler"
        })

    return orders_on_page, raw_texts

# ==========================================
# 4. PDF BASTIRMA
# ==========================================
def build_grouped_a4_pdf(grouped_receipts, output_pdf_path, tasarruf_modu):
    doc = SimpleDocTemplate(output_pdf_path, pagesize=A4, leftMargin=12, rightMargin=12, topMargin=8, bottomMargin=8)
    styles = getSampleStyleSheet()
    
    recipient_style = ParagraphStyle('Recip', parent=styles['Normal'], fontSize=8.5, leading=10.5, fontName=BOLD_FONT, textColor=colors.HexColor("#2C3E50"))
    address_style = ParagraphStyle('Addr', parent=styles['Normal'], fontSize=6.5, leading=8.5, fontName=MAIN_FONT, textColor=colors.HexColor("#566573"))
    order_style = ParagraphStyle('Ord', parent=styles['Normal'], fontSize=7.0, leading=9.0, fontName=MAIN_FONT, textColor=colors.HexColor("#7F8C8D"))
    multi_style = ParagraphStyle('Multi', parent=styles['Normal'], fontSize=8.0, leading=10, fontName=BOLD_FONT, textColor=colors.HexColor("#FFFFFF"), backColor=colors.HexColor("#C0392B"), alignment=1)
    text_style = ParagraphStyle('Txt', parent=styles['Normal'], fontSize=6.8, leading=9.0, fontName=MAIN_FONT, textColor=colors.HexColor("#34495E"))
    bc_text_style = ParagraphStyle('BC', parent=styles['Normal'], fontSize=8.5, leading=11, fontName=BOLD_FONT, alignment=1, textColor=colors.HexColor("#2C3E50"))
    
    story = []
    all_label_cells = []
    
    for group_key, items in grouped_receipts.items():
        for item in items:
            cell_elements = []
            
            cell_elements.append(Paragraph(f"<b>ALICI:</b> {item['recipient']}", recipient_style))
            cell_elements.append(Paragraph(f"<b>ADRES:</b> {item['address']}", address_style))
            cell_elements.append(Paragraph(f"Sipariş No: {item['order_no']} | <b>Kargo: {item['cargo_company']}</b>", order_style))
            
            if item.get("cargo_logo_bytes"):
                try:
                    logo_io = BytesIO(item["cargo_logo_bytes"])
                    pil_img = Image.open(BytesIO(item["cargo_logo_bytes"]))
                    orig_w, orig_h = pil_img.size
                    
                    if orig_h > 0:
                        aspect = orig_w / orig_h
                        target_h = 36
                        target_w = min(150, int(target_h * aspect))
                    else:
                        target_w, target_h = 130, 36
                        
                    logo_img = RLImage(logo_io, width=target_w, height=target_h)
                    cell_elements.append(Spacer(1, 2))
                    cell_elements.append(logo_img)
                except Exception:
                    pass

            cell_elements.append(Spacer(1, 2))
            
            if item['cargo_barcode'] != "N/A":
                try:
                    bc = code128.Code128(item['cargo_barcode'], barHeight=32, barWidth=1.0)
                    cell_elements.append(bc)
                    cell_elements.append(Paragraph(f"{item['cargo_barcode']}", bc_text_style))
                except: cell_elements.append(Paragraph(f"Kargo Kodu: {item['cargo_barcode']}", bc_text_style))
            else:
                cell_elements.append(Paragraph("<b>[Kargo Barkodu Okunamadı]</b>", recipient_style))

            cell_elements.append(Spacer(1, 2))
            if item["is_multi"]:
                cell_elements.append(Paragraph("<b>⚠️ DİKKAT: ÇOKLU SİPARİŞ ⚠️</b>", multi_style))
                cell_elements.append(Spacer(1, 2))
            
            products_html = ""
            for p in item["products"]:
                name = p['name'][:77] + "..." if len(p['name']) > 80 else p['name']
                products_html += f"<font color='#2980B9'><b>• Ürün #{p['index']}:</b></font> {name}<br/>"
                products_html += f"&nbsp;&nbsp;<b>Adet:</b> <font color='#C0392B'><b>{p['qty']}</b></font> | <b>Renk:</b> {p['color']} | <b>Beden:</b> {p['size']}<br/>"
                products_html += f"&nbsp;&nbsp;<b>Barkod:</b> {p['barcode']} | <b>Stok Kodu:</b> {p['sku']}<br/><br/>"

            cell_elements.append(Paragraph(products_html.strip(), text_style))
            all_label_cells.append(cell_elements)

    for i in range(0, len(all_label_cells), 6):
        page_chunk = all_label_cells[i:i+6]
        while len(page_chunk) < 6: page_chunk.append([Paragraph("", text_style)])
        grid_matrix = [[page_chunk[0], page_chunk[1]], [page_chunk[2], page_chunk[3]], [page_chunk[4], page_chunk[5]]]
        table = Table(grid_matrix, colWidths=[285, 285], rowHeights=[262, 262, 262])
        table.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor("#34495E")),
            ('INNERGRID', (0,0), (-1,-1), 0.6, colors.HexColor("#BDC3C7")),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFFFFF")),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(table)
        if i + 6 < len(all_label_cells): story.append(PageBreak())

    doc.build(story)

# ==========================================
# 5. KULLANICI ARAYÜZÜ (ÖNİZLEME VE DEBUG)
# ==========================================
if 'processed_data' not in st.session_state: st.session_state.processed_data = None
if 'raw_debug_texts' not in st.session_state: st.session_state.raw_debug_texts = None
if 'debug_mode' not in st.session_state: st.session_state.debug_mode = False

st.sidebar.header("⚙️ Ayarlar")
sayfa_tasarruf = st.sidebar.checkbox("Sayfa Tasarruf Modu", value=True)

uploaded_files = st.file_uploader("PDF Yükleyin", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("Verileri Oku ve Tabloyu Oluştur"):
        with st.spinner("PDF'ler kural tabanlı engine ile analiz ediliyor..."):
            all_orders, all_raw_texts = [], []
            with tempfile.TemporaryDirectory() as temp_dir:
                for uf in uploaded_files:
                    path = os.path.join(temp_dir, uf.name)
                    with open(path, "wb") as f: f.write(uf.getbuffer())
                    orders, raw_texts = extract_order_data_from_pdf(path)
                    all_orders.extend(orders)
                    all_raw_texts.extend(raw_texts)
            
            st.session_state.processed_data = all_orders
            st.session_state.raw_debug_texts = all_raw_texts
            st.session_state.debug_mode = False

if st.session_state.processed_data:
    st.markdown("---")
    st.subheader("👀 Tüm Siparişlerin Önizlemesi ")
    
    preview_data = []
    for ord_item in st.session_state.processed_data:
        for p in ord_item['products']:
            preview_data.append({
                "Alıcı": ord_item["recipient"],
                "Kargo Şirketi": ord_item["cargo_company"],
                "Ürün Adı": p["name"],
                "Adet (GERÇEK)": p["qty"],
                "Renk": p["color"],
                "Beden": p["size"],
                "Barkod": p["barcode"],
                "Stok Kodu": p["sku"],
                "Sipariş No": ord_item["order_no"]
            })
    
    st.dataframe(preview_data, use_container_width=True)

    st.markdown("---")
    c1, c2 = st.columns(2)
    
    with c1:
        if st.button("✅ Her Şey Doğru, PDF'i İndir", type="primary"):
            with tempfile.TemporaryDirectory() as temp_dir:
                output_pdf_path = os.path.join(temp_dir, "Baski_Etiketler.pdf")
                grouped = defaultdict(list)
                for ord_item in st.session_state.processed_data:
                    grouped[ord_item["group_key"]].append(ord_item)
                
                build_grouped_a4_pdf(grouped, output_pdf_path, sayfa_tasarruf)
                
                with open(output_pdf_path, "rb") as f: pdf_bytes = f.read()
                st.download_button(label="📥 Etiket PDF'ini İndir", data=pdf_bytes, file_name="Hazir_Etiketler.pdf", mime="application/pdf")
    
    with c2:
        if st.button("🛠️ Cropping & Logo Tespiti Kontrolü (Debug Modu)"):
            st.session_state.debug_mode = not st.session_state.debug_mode

    if st.session_state.debug_mode:
        st.error("🛠️ Logo Kırpma (Cropping) ve Hata Ayıklama Modu")
        
        page_options = [f"Sayfa {item['page']}" for item in st.session_state.raw_debug_texts]
        selected_page = st.selectbox("İncelemek İstediğiniz Sayfayı Seçin:", page_options)
        
        page_idx = page_options.index(selected_page)
        page_info = st.session_state.raw_debug_texts[page_idx]
        
        st.write(f"**Tespit Edilen Kargo:** `{page_info['detected_cargo']}`")
        if page_info['cropped_img']:
            st.write("**Sayfadan Kırpılan Kargo Logosu Bölgesi:**")
            st.image(page_info['cropped_img'], width=300)
            
        st.text_area("Sayfa Ham Metni", page_info["text"], height=200)
