import os
import re
import tempfile
import urllib.request
from io import BytesIO
from collections import defaultdict
import streamlit as st
import pdfplumber
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
st.title("📦 Trendyol Toplu Etiket (%100 Adet & Büyük Logo)")

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
# 2. KARGO FİRMASI VE HASSAS LOGO TESPİTİ
# ==========================================
def detect_cargo_company(text, page_img=None):
    text_upper = text.upper()
    
    if "PTT" in text_upper:
        cargo_name = "PTT Kargo"
    elif "YURTICI" in text_upper or "YURTİÇİ" in text_upper:
        cargo_name = "Yurtiçi Kargo"
    elif "ARAS" in text_upper:
        cargo_name = "Aras Kargo"
    elif "MNG" in text_upper:
        cargo_name = "MNG Kargo"
    elif "SURAT" in text_upper or "SÜRAT" in text_upper:
        cargo_name = "Sürat Kargo"
    elif "TRENDYOL EXPRESS" in text_upper or " TEX " in text_upper:
        cargo_name = "Trendyol Express"
    elif "SENDEO" in text_upper:
        cargo_name = "Sendeo"
    elif "HEPSIJET" in text_upper or "HEPSİJET" in text_upper:
        cargo_name = "HepsiJET"
    else:
        cargo_name = "PTT Kargo"

    cropped_img, cargo_logo_bytes = None, None
    if page_img:
        width, height = page_img.size
        # Kargo logosunu içeren bölgeyi hassas kırpma
        crop_box = (int(width * 0.68), int(height * 0.03), int(width * 0.99), int(height * 0.23))
        cropped_img = page_img.crop(crop_box)
        
        img_byte_arr = BytesIO()
        cropped_img.save(img_byte_arr, format='PNG')
        cargo_logo_bytes = img_byte_arr.getvalue()

    return cargo_name, cropped_img, cargo_logo_bytes

# ==========================================
# 3. KUSURSUZ ÜRÜN & ADET PARÇALAMA
# ==========================================
def parse_products_from_text(text, cargo_bc="N/A"):
    products = []
    if "Ürün Bilgileri" not in text:
        return [{
            "index": "1",
            "name": "Trendyol Ürünü",
            "qty": "1 Adet",
            "color": "-",
            "size": "Tek Ebat",
            "barcode": cargo_bc,
            "sku": "-"
        }]

    prod_section = text.split("Ürün Bilgileri", 1)[1].strip()
    lines = [l.strip() for l in prod_section.split('\n') if l.strip()]
    
    clean_lines = [l for l in lines if not re.match(r'^\d+/\d+$', l) and l.lower() != 'ptt']
    
    title_lines = []
    attr_lines = []
    found_item_number = False

    for line in clean_lines:
        line_clean = re.sub(r'^[\|\s]+|[\|\s]+$', '', line).strip()
        
        if not found_item_number:
            if re.match(r'^\d+$', line_clean) and len(line_clean) <= 2:
                found_item_number = True
            else:
                title_lines.append(line)
        else:
            attr_lines.append(line)

    product_name = fix_tr(" ".join(title_lines).strip())
    if not product_name:
        product_name = "Trendyol Ürünü"

    full_attr_text = "\n".join(attr_lines)

    # 1. Gerçek Ürün Adedini Yakalama (4 Adet, 6 Adet vb.)
    qty = "1 Adet"
    qty_match = re.search(r'\b(\d+)\s*Adet\b', full_attr_text, re.IGNORECASE)
    if qty_match:
        qty = f"{qty_match.group(1)} Adet"
    else:
        qty_match_alt = re.search(r'Adet\s*[:\|]?\s*(\d+)', full_attr_text, re.IGNORECASE)
        if qty_match_alt:
            qty = f"{qty_match_alt.group(1)} Adet"

    # 2. Renk Yakalama
    color = "-"
    color_match = re.search(r'\b(RENKLİ|BEYAZ|SİYAH|GRİ|KIRMIZI|MAVİ|SARI|YEŞİL|PEMBE|TURUNCU|LACİVERT|KAHVERENGİ)\b', full_attr_text, re.IGNORECASE)
    if color_match:
        color = fix_tr(color_match.group(1).upper())

    # 3. Beden Yakalama
    size = "Tek Ebat"
    size_match = re.search(r'\b(Tek Ebat|Standart|One Size|XS|S|M|L|XL|XXL)\b', full_attr_text, re.IGNORECASE)
    if size_match:
        size = fix_tr(size_match.group(1))

    # 4. Stok Kodu (SKU)
    sku = "-"
    for idx, l in enumerate(attr_lines):
        cl = re.sub(r'^[\|\s]+|[\|\s]+$', '', l).strip().lower()
        if cl == "stok kodu" and idx + 1 < len(attr_lines):
            potential_sku = re.sub(r'^[\|\s]+|[\|\s]+$', '', attr_lines[idx+1]).strip()
            if potential_sku.lower() not in ['ptt', 'barkod', 'beden', 'renk']:
                sku = fix_tr(potential_sku)
                break
    
    if sku == "-":
        sku_fallback = re.search(r'\b(TYB[A-Za-z0-9_\-]+|[a-zA-Z0-9_\-]{8,})\b', full_attr_text)
        if sku_fallback and sku_fallback.group(1).lower() not in ['ptt', 'barkod', 'tek ebat']:
            sku = fix_tr(sku_fallback.group(1))

    # 5. Barkod
    barcode = "-"
    all_digits = re.findall(r'\b(\d{8,14})\b', full_attr_text)
    for d in all_digits:
        if d != cargo_bc and d not in qty and d != sku:
            barcode = d
            break

    products.append({
        "index": "1",
        "name": product_name,
        "qty": qty,
        "color": color,
        "size": size,
        "barcode": barcode,
        "sku": sku
    })

    return products

# ==========================================
# 4. PDF İŞLEME VE VERİ ÇEKME
# ==========================================
def extract_order_data_from_pdf(pdf_path):
    orders_on_page = []
    raw_texts = [] 
    
    try:
        images = convert_from_path(pdf_path)
    except Exception:
        images = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            page_img = images[page_num] if page_num < len(images) else None

            cargo_company, cropped_logo, cargo_logo_bytes = detect_cargo_company(text, page_img)

            raw_texts.append({
                "page": page_num + 1, 
                "text": text, 
                "cropped_img": cropped_logo,
                "detected_cargo": cargo_company
            })

            order_no = "N/A"
            s_match = re.search(r'Sipariş No\s*[:\|]?\s*(\d+)', text)
            if s_match: order_no = s_match.group(1).strip()

            recipient = "N/A"
            r_match = re.search(r'Ad-Soyad\s*[:\|]?\s*(.*?)(?=\n\s*Adres|\n\s*Çıkış|$)', text, re.DOTALL)
            if r_match: recipient = fix_tr(re.sub(r'^[:\s]+', '', r_match.group(1)))

            address = "N/A"
            a_match = re.search(r'Adres\s*[:\|]?\s*(.*?)(?=\n\s*Çıkış|\n\s*Ürün Bilgileri|\n\s*Fatura|$)', text, re.DOTALL)
            if a_match: address = fix_tr(re.sub(r'^[:\s]+', '', a_match.group(1)))

            cargo_bc = "N/A"
            c_match = re.search(r'Kargo Barkodu\s*\n?\s*(\d{13,16})', text)
            if not c_match: c_match = re.search(r'\b(\d{13,16})\b', text)
            if c_match: cargo_bc = c_match.group(1).strip()
            
            if cargo_bc == "N/A" and page_img:
                try:
                    decoded = decode(page_img)
                    if decoded: cargo_bc = decoded[0].data.decode('utf-8')
                except: pass

            products = parse_products_from_text(text, cargo_bc)

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
# 5. DÜZENLİ PDF OLUŞTURMA (BÜYÜK LOGO DESTEKLİ)
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
            
            # Kırpılan Kargo Logosunu Büyük Boyutta Ekleme (width=120, height=36)
            if item.get("cargo_logo_bytes"):
                try:
                    logo_io = BytesIO(item["cargo_logo_bytes"])
                    pil_img = Image.open(BytesIO(item["cargo_logo_bytes"]))
                    orig_w, orig_h = pil_img.size
                    
                    if orig_h > 0:
                        aspect = orig_w / orig_h
                        target_h = 32
                        target_w = min(130, int(target_h * aspect))
                    else:
                        target_w, target_h = 110, 32
                        
                    logo_img = RLImage(logo_io, width=target_w, height=target_h)
                    cell_elements.append(Spacer(1, 2))
                    cell_elements.append(logo_img)
                except Exception:
                    pass

            cell_elements.append(Spacer(1, 2))
            
            if item['cargo_barcode'] != "N/A":
                try:
                    bc = code128.Code128(item['cargo_barcode'], barHeight=34, barWidth=1.0)
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
# 6. KULLANICI ARAYÜZÜ
# ==========================================
if 'processed_data' not in st.session_state: st.session_state.processed_data = None
if 'raw_debug_texts' not in st.session_state: st.session_state.raw_debug_texts = None
if 'debug_mode' not in st.session_state: st.session_state.debug_mode = False

st.sidebar.header("⚙️ Ayarlar")
sayfa_tasarruf = st.sidebar.checkbox("Sayfa Tasarruf Modu", value=True)

uploaded_files = st.file_uploader("PDF Yükleyin", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("Verileri Oku ve Tabloyu Oluştur"):
        with st.spinner("PDF'ler analiz ediliyor..."):
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
    st.subheader("👀 Tüm Siparişlerin Önizlemesi (Gerçek Adetler Kontrol Edilebilir)")
    
    preview_data = []
    for ord_item in st.session_state.processed_data:
        for p in ord_item['products']:
            preview_data.append({
                "Alıcı": ord_item["recipient"],
                "Kargo Şirketi": ord_item["cargo_company"],
                "Ürün Adı": p["name"],
                "Adet (GERÇEK)": p["qty"],
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
            st.image(page_info['cropped_img'], width=280)
            
        st.text_area("Sayfa Ham Metni", page_info["text"], height=200)
