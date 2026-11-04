import os
import re
import tempfile
import urllib.request
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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.barcode import code128
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ==========================================
# 1. ARAYÜZ VE TÜRKÇE FONT AYARLARI
# ==========================================
st.set_page_config(page_title="Trendyol Etiket Düzenleyici", page_icon="📦", layout="wide")
st.title("📦 Trendyol Toplu Etiket (Cropping & Kargo Logo Tespiti)")

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
# 2. BÖLGESEL KIRPMA (CROPPING) & KARGO TESPİTİ
# ==========================================
def detect_cargo_company_from_crop(page_img):
    """
    PDF sayfasının sağ üst köşesinden kargo logosunun olduğu bölgeyi kırpar
    ve OCR / metin taraması ile kargo firmasını tespit eder.
    """
    if not page_img:
        return "Bilinmiyor", None

    width, height = page_img.size
    
    # Trendyol Etiketi Sağ Üst Köşe Kırpma Alanı (Sağ %25, Üst %18)
    # Box: (sol, üst, sağ, alt)
    crop_box = (int(width * 0.72), int(height * 0.02), int(width * 0.98), int(height * 0.18))
    cropped_img = page_img.crop(crop_box)

    cargo_name = "Bilinmiyor"
    
    if pytesseract:
        try:
            # Kırpılan logodan metin oku
            ocr_text = pytesseract.image_to_string(cropped_img, lang='tur+eng').upper()
            
            if "PTT" in ocr_text:
                cargo_name = "PTT Kargo"
            elif "YURTICI" in ocr_text or "YURTİÇİ" in ocr_text:
                cargo_name = "Yurtiçi Kargo"
            elif "ARAS" in ocr_text:
                cargo_name = "Aras Kargo"
            elif "MNG" in ocr_text:
                cargo_name = "MNG Kargo"
            elif "SURAT" in ocr_text or "SÜRAT" in ocr_text:
                cargo_name = "Sürat Kargo"
            elif "EXPRESS" in ocr_text or "TEX" in ocr_text or "TRENDYOL" in ocr_text:
                cargo_name = "Trendyol Express"
            elif "SENDEO" in ocr_text:
                cargo_name = "Sendeo"
            elif "HEPSIJET" in ocr_text or "JET" in ocr_text:
                cargo_name = "HepsiJET"
        except Exception:
            pass

    return cargo_name, cropped_img

# ==========================================
# 3. PDF İŞLEME VE VERİ ÇEKME
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

            # 1. Kargo Logosunu Kırpma ve Tespit Etme
            cargo_company, cropped_logo = detect_cargo_company_from_crop(page_img)

            # Eğer OCR ile bulunamadıysa metin içindeki yedek tarama
            if cargo_company == "Bilinmiyor":
                text_upper = text.upper()
                if "PTT" in text_upper: cargo_company = "PTT Kargo"
                elif "YURTICI" in text_upper or "YURTİÇİ" in text_upper: cargo_company = "Yurtiçi Kargo"
                elif "ARAS" in text_upper: cargo_company = "Aras Kargo"
                elif "MNG" in text_upper: cargo_company = "MNG Kargo"

            raw_texts.append({
                "page": page_num + 1, 
                "text": text, 
                "cropped_img": cropped_logo,
                "detected_cargo": cargo_company
            })

            # Sipariş No, Ad, Adres, Barkod
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

            # Ürün Bilgileri
            products = []
            if "Ürün Bilgileri" in text:
                prod_section = text.split("Ürün Bilgileri", 1)[1].strip()
                lines = [l.strip() for l in prod_section.split('\n') if l.strip()]
                
                product_name_lines = []
                qty, color, size, barcode, sku = "1 Adet", "-", "Tek Ebat", "-", "-"
                
                i = 0
                while i < len(lines):
                    line = lines[i]
                    cl = re.sub(r'^\|\s*', '', line).strip()
                    cl_lower = cl.lower()
                    
                    if cl_lower == 'ptt' or re.match(r'^\d+/\d+$', cl): 
                        i += 1; continue
                    if re.match(r'^\d+$', cl) and len(cl) < 3: 
                        i += 1; continue
                    if cl_lower in ["adet", "renk", "beden", "barkod"]: 
                        i += 1; continue
                        
                    if cl_lower == "stok kodu":
                        if i + 1 < len(lines):
                            sku = lines[i+1].strip()
                            if sku.lower() == 'ptt': sku = "-"
                            
                            name = " ".join(product_name_lines).strip()
                            products.append({
                                "index": str(len(products)+1),
                                "name": name if name else "Trendyol Urunu",
                                "qty": qty, "color": color, "size": size, "barcode": barcode, "sku": sku
                            })
                            product_name_lines = []
                            qty, color, size, barcode, sku = "1 Adet", "-", "Tek Ebat", "-", "-"
                            i += 2
                            continue
                        else:
                            i += 1; continue
                            
                    if re.match(r'^\d+\s*adet$', cl_lower): qty = cl; i += 1; continue
                    if re.match(r'^\d{6,}$', cl): barcode = cl; i += 1; continue
                    if cl_lower in ["tek ebat", "standart", "one size"]: size = cl; i += 1; continue
                    if cl_lower in ["renkli", "beyaz", "siyah", "kırmızı", "mavi", "sarı", "yeşil", "gri", "pembe", "-"]: color = cl; i += 1; continue
                        
                    product_name_lines.append(line)
                    i += 1

                if product_name_lines:
                    name = " ".join(product_name_lines).strip()
                    products.append({
                        "index": str(len(products)+1),
                        "name": name if name else "Trendyol Urunu",
                        "qty": qty, "color": color, "size": size, "barcode": barcode, "sku": sku
                    })

            if not products:
                products.append({"index": "1", "name": recipient + " Siparişi", "qty": "1 Adet", "color": "-", "size": "Tek Ebat", "barcode": cargo_bc, "sku": "-"})

            total_qty = sum([int(re.search(r'\d+', p["qty"]).group(0)) for p in products if re.search(r'\d+', p["qty"])])
            is_multi = len(products) > 1 or total_qty > 1
            
            orders_on_page.append({
                "order_no": order_no, "recipient": recipient, "address": address,
                "cargo_barcode": cargo_bc, "cargo_company": cargo_company, "products": products, "is_multi": is_multi, 
                "group_key": products[0]["name"] if products else "Diger_Urunler"
            })

    return orders_on_page, raw_texts

# ==========================================
# 4. PDF OLUŞTURMA FONKSİYONU
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
# 5. KULLANICI ARAYÜZÜ (TABLO & CROPPING DEBUG)
# ==========================================
if 'processed_data' not in st.session_state: st.session_state.processed_data = None
if 'raw_debug_texts' not in st.session_state: st.session_state.raw_debug_texts = None
if 'debug_mode' not in st.session_state: st.session_state.debug_mode = False

st.sidebar.header("⚙️ Ayarlar")
sayfa_tasarruf = st.sidebar.checkbox("Sayfa Tasarruf Modu", value=True)

uploaded_files = st.file_uploader("PDF Yükleyin", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("Verileri Oku ve Tabloyu Oluştur"):
        with st.spinner("PDF'ler analiz ediliyor ve kargo logoları kırpılıyor..."):
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
    st.subheader("👀 Tüm Siparişlerin Önizlemesi")
    
    preview_data = []
    for ord_item in st.session_state.processed_data:
        for p in ord_item['products']:
            preview_data.append({
                "Alıcı": ord_item["recipient"],
                "Kargo Şirketi": ord_item["cargo_company"],
                "Ürün Adı": p["name"],
                "Adet": p["qty"],
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

    # Debug Modu
    if st.session_state.debug_mode:
        st.error("🛠️ Logo Kırpma (Cropping) ve Hata Ayıklama Modu")
        
        page_options = [f"Sayfa {item['page']}" for item in st.session_state.raw_debug_texts]
        selected_page = st.selectbox("İncelemek İstediğiniz Sayfayı Seçin:", page_options)
        
        page_idx = page_options.index(selected_page)
        page_info = st.session_state.raw_debug_texts[page_idx]
        
        st.write(f"**Tespit Edilen Kargo:** `{page_info['detected_cargo']}`")
        if page_info['cropped_img']:
            st.write("**Sayfadan Kırpılan Kargo Logosu Bölgesi:**")
            st.image(page_info['cropped_img'], width=250)
            
        st.text_area("Sayfa Ham Metni", page_info["text"], height=200)
