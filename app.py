import os
import re
import tempfile
import urllib.request
from collections import defaultdict
import streamlit as st
import pdfplumber
from pdf2image import convert_from_path
from pyzbar.pyzbar import decode

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.barcode import code128
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Sayfa Yapılandırması
st.set_page_config(page_title="Trendyol Etiket Düzenleyici", page_icon="📦", layout="wide")
st.title("📦 Trendyol Toplu Etiket (Önizlemeli & %100 Türkçe)")

# ==========================================
# 1. KÜRESEL TÜRKÇE FONT ÇÖZÜMÜ (ROBOTO)
# ==========================================
@st.cache_resource
def load_turkish_fonts():
    font_path = "Roboto-Regular.ttf"
    bold_font_path = "Roboto-Bold.ttf"
    
    # Fontlar yoksa indir (Bulut sunucular için %100 çözüm)
    if not os.path.exists(font_path):
        urllib.request.urlretrieve("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf", font_path)
    if not os.path.exists(bold_font_path):
        urllib.request.urlretrieve("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Bold.ttf", bold_font_path)
        
    pdfmetrics.registerFont(TTFont('Roboto', font_path))
    pdfmetrics.registerFont(TTFont('Roboto-Bold', bold_font_path))
    return 'Roboto', 'Roboto-Bold'

MAIN_FONT, BOLD_FONT = load_turkish_fonts()

# ==========================================
# 2. VERİ ÇEKME VE PDF İŞLEME
# ==========================================
def extract_order_data_from_pdf(pdf_path):
    orders_on_page = []
    raw_texts = [] # Debug modu için ham metinleri tutacağız
    
    try:
        images = convert_from_path(pdf_path)
    except Exception:
        images = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            raw_texts.append(text)
            page_img = images[page_num] if page_num < len(images) else None

            order_no = "N/A"
            s_match = re.search(r'Sipariş No\s*[:\|]?\s*(\d+)', text)
            if s_match: order_no = s_match.group(1).strip()

            recipient = "N/A"
            r_match = re.search(r'Ad-Soyad\s*[:\|]?\s*(.*?)(?=\n\s*Adres|\n\s*Çıkış|$)', text, re.DOTALL)
            if r_match: recipient = re.sub(r'^[:\s]+', '', r_match.group(1)).strip().replace('\n', ' ')

            address = "N/A"
            a_match = re.search(r'Adres\s*[:\|]?\s*(.*?)(?=\n\s*Çıkış|\n\s*Ürün Bilgileri|\n\s*Fatura|$)', text, re.DOTALL)
            if a_match: address = re.sub(r'^[:\s]+', '', a_match.group(1)).strip().replace('\n', ' ')

            cargo_bc = "N/A"
            c_match = re.search(r'Kargo Barkodu\s*\n?\s*(\d{13,16})', text)
            if not c_match: c_match = re.search(r'\b(\d{13,16})\b', text)
            if c_match: cargo_bc = c_match.group(1).strip()
            
            if cargo_bc == "N/A" and page_img:
                try:
                    decoded = decode(page_img)
                    if decoded: cargo_bc = decoded[0].data.decode('utf-8')
                except: pass

            products = []
            if "Ürün Bilgileri" in text:
                prod_section = text.split("Ürün Bilgileri", 1)[1].strip()
                lines = [l.strip() for l in prod_section.split('\n') if l.strip()]
                product_counter = 1
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if "Adet" in line or "Tek Ebat" in line or "adet" in line.lower():
                        product_name = ""
                        for lookback in range(1, 4):
                            if i - lookback >= 0:
                                candidate = lines[i - lookback]
                                if candidate and not re.match(r'^\d+$', candidate) and not "Ürün" in candidate and not "Adet" in candidate:
                                    product_name = candidate
                                    break
                        if not product_name: product_name = "Trendyol Urunu"

                        tokens = [t for t in line.split() if t != "-1"]
                        qty, color, size, barcode, sku = "1 Adet", "-", "Tek Ebat", "-", "-"
                        
                        has_valid_identifier = False
                        for idx, tok in enumerate(tokens):
                            if "adet" in tok.lower() and idx > 0: qty = f"{tokens[idx-1]} {tok}"
                            elif tok.isdigit() and len(tok) >= 12: barcode, has_valid_identifier = tok, True
                            elif tok.startswith("TYC") or tok.startswith("AD") or len(tok) > 10: sku, has_valid_identifier = tok, True

                        if has_valid_identifier or sku != "-" or barcode != "-":
                            products.append({
                                "index": str(product_counter), "name": product_name, "qty": qty,
                                "color": color, "size": size, "barcode": barcode, "sku": sku
                            })
                            product_counter += 1
                    i += 1

            if not products:
                products.append({"index": "1", "name": recipient + " Siparişi", "qty": "1 Adet", "color": "-", "size": "Tek Ebat", "barcode": cargo_bc, "sku": "-"})

            total_qty = sum([int(re.search(r'\d+', p["qty"]).group(0)) for p in products if re.search(r'\d+', p["qty"])])
            is_multi = len(products) > 1 or total_qty > 1
            group_key = products[0]["name"] if products else "Diger_Urunler"

            orders_on_page.append({
                "order_no": order_no, "recipient": recipient, "address": address,
                "cargo_barcode": cargo_bc, "products": products, "is_multi": is_multi, "group_key": group_key
            })

    return orders_on_page, raw_texts

def build_grouped_a4_pdf(grouped_receipts, output_pdf_path, tasarruf_modu):
    doc = SimpleDocTemplate(output_pdf_path, pagesize=A4, leftMargin=12, rightMargin=12, topMargin=8, bottomMargin=8)
    styles = getSampleStyleSheet()
    
    # Yeni fontlar tanımlanıyor (fix_tr kullanmaya gerek kalmadı!)
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
            cell_elements.append(Paragraph(f"Sipariş No: {item['order_no']}", order_style))
            cell_elements.append(Spacer(1, 2))
            
            if item['cargo_barcode'] != "N/A":
                try:
                    bc = code128.Code128(item['cargo_barcode'], barHeight=42, barWidth=1.1)
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
# 3. KULLANICI ARAYÜZÜ & ÖNİZLEME/DEBUG
# ==========================================
if 'processed_data' not in st.session_state: st.session_state.processed_data = None
if 'raw_debug_texts' not in st.session_state: st.session_state.raw_debug_texts = None

st.sidebar.header("⚙️ Ayarlar")
sayfa_tasarruf = st.sidebar.checkbox("Sayfa Tasarruf Modu", value=True)

uploaded_files = st.file_uploader("PDF Yükleyin", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("Verileri Oku ve Önizleme Oluştur"):
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

if st.session_state.processed_data:
    st.markdown("---")
    st.subheader("👀 Sistem Tarafından Okunan Örnek 1. Etiket")
    first_order = st.session_state.processed_data[0]
    
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"**Alıcı:** {first_order['recipient']}\n\n**Sipariş No:** {first_order['order_no']}\n\n**Kargo Kodu:** {first_order['cargo_barcode']}")
    with col2:
        st.warning(f"**Adres:** {first_order['address']}")
    
    st.write("**Yakalanan Ürünler:**")
    st.json(first_order['products'])

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
        if st.button("🛠️ Veriler Hatalı/Eksik (Debug Modunu Aç)"):
            st.error("Geliştirici Hata Ayıklama Modu Açıldı. Aşağıdaki ham metin, Trendyol PDF'inin kod tarafından nasıl görüldüğünü gösterir:")
            st.text_area("PDF Sayfa 1 Ham Metni", st.session_state.raw_debug_texts[0], height=300)
