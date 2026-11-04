import os
import re
import tempfile
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

# Sayfa Yapılandırması
st.set_page_config(page_title="Trendyol Etiket Düzenleyici", page_icon="📦", layout="centered")

st.title("📦 Trendyol Toplu Etiket ve Fiş Dönüştürücü")
st.write("Trendyol güncel şablonu için optimize edildi; ürün adı ve adres sorunları giderildi.")

# Yan Menü (Sidebar) Ayarları
st.sidebar.header("⚙️ Baskı Ayarları")
sayfa_tasarruf = st.sidebar.checkbox(
    "Sayfa Tasarruf Modu", 
    value=True, 
    help="Etiketleri aralarında boşluk bırakmadan sayfaları tam dolduracak şekilde sıkılaştırır ve kağıt tasarrufu sağlar."
)

MAIN_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

def fix_tr(text):
    """Türkçe karakterleri PDF fontunun (Helvetica) bozulmaması için uyumlu hale getirir."""
    if not isinstance(text, str):
        return str(text)
    tr_map = {
        'ş': 's', 'Ş': 'S',
        'ğ': 'g', 'Ğ': 'G',
        'İ': 'I', 'ı': 'i',
        'ö': 'o', 'Ö': 'O',
        'ü': 'u', 'Ü': 'U',
        'ç': 'c', 'Ç': 'C'
    }
    for k, v in tr_map.items():
        text = text.replace(k, v)
    return text

def extract_order_data_from_pdf(pdf_path):
    orders_on_page = []
    try:
        images = convert_from_path(pdf_path)
    except Exception:
        images = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            page_img = images[page_num] if page_num < len(images) else None

            # 1. Sipariş No
            order_no = "N/A"
            s_match = re.search(r'Sipariş No\s*[:\|]?\s*(\d+)', text)
            if s_match:
                order_no = s_match.group(1).strip()

            # 2. Alıcı Adı
            recipient = "N/A"
            r_match = re.search(r'Ad-Soyad\s*[:\|]?\s*(.*?)(?=\n\s*Adres|\n\s*Çıkış|$)', text, re.DOTALL)
            if r_match:
                recipient = fix_tr(re.sub(r'^[:\s]+', '', r_match.group(1)).strip().replace('\n', ' '))

            # 3. Adres Bilgisi
            address = "N/A"
            a_match = re.search(r'Adres\s*[:\|]?\s*(.*?)(?=\n\s*Çıkış|\n\s*Ürün Bilgileri|\n\s*Fatura|$)', text, re.DOTALL)
            if a_match:
                address = fix_tr(re.sub(r'^[:\s]+', '', a_match.group(1)).strip().replace('\n', ' '))

            # 4. Kargo Barkodu
            cargo_bc = "N/A"
            c_match = re.search(r'Kargo Barkodu\s*\n?\s*(\d{13,16})', text)
            if not c_match:
                c_match = re.search(r'\b(\d{13,16})\b', text)
            if c_match:
                cargo_bc = c_match.group(1).strip()
            
            if cargo_bc == "N/A" and page_img:
                try:
                    decoded = decode(page_img)
                    if decoded:
                        cargo_bc = decoded[0].data.decode('utf-8')
                except Exception:
                    pass

            # 5. Ürün Bilgileri (Güncel Şablon İçin Güçlendirilmiş Parçalama)
            products = []
            if "Ürün Bilgileri" in text:
                prod_section = text.split("Ürün Bilgileri", 1)[1].strip()
                lines = [l.strip() for l in prod_section.split('\n') if l.strip()]
                
                product_counter = 1
                i = 0
                while i < len(lines):
                    line = lines[i]
                    
                    # Eğer satırda "Adet" geçiyorsa veya standart bir ürün detay satırıysa
                    if "Adet" in line or "Tek Ebat" in line or "Adet" in line.lower():
                        # Bu satırdan hemen önceki anlamlı metin ürün adıdır
                        product_name = "Trendyol Urunu"
                        for lookback in range(1, 4):
                            if i - lookback >= 0:
                                candidate = lines[i - lookback]
                                # Sayısal sıra numaralarını veya başlıkları atla
                                if candidate and not re.match(r'^\d+$', candidate) and not "Ürün" in candidate and not "Adet" in candidate:
                                    product_name = fix_tr(candidate)
                                    break
                        
                        tokens = [t for t in line.split() if t != "-1"]
                        qty, color, size, barcode, sku = "1 Adet", "-", "Tek Ebat", "-", "-"
                        
                        for idx, tok in enumerate(tokens):
                            if "adet" in tok.lower() and idx > 0:
                                qty = f"{tokens[idx-1]} {tok}"
                            elif tok.isdigit() and len(tok) >= 12:
                                barcode = tok
                            elif tok.startswith("TYC") or tok.startswith("AD") or len(tok) > 10:
                                sku = tok

                        products.append({
                            "index": str(product_counter),
                            "name": product_name,
                            "qty": qty,
                            "color": color,
                            "size": size,
                            "barcode": barcode,
                            "sku": sku
                        })
                        product_counter += 1
                    i += 1

            # Eğer ürün listesi boş kalırsa metinden alternatif olarak çekmeye çalış
            if not products:
                products.append({
                    "index": "1",
                    "name": fix_tr(recipient) + " Siparişi",
                    "qty": "1 Adet",
                    "color": "-",
                    "size": "Tek Ebat",
                    "barcode": cargo_bc,
                    "sku": "-"
                })

            total_qty = 0
            for p in products:
                qm = re.search(r'\d+', p["qty"])
                if qm: total_qty += int(qm.group(0))
            
            is_multi = len(products) > 1 or total_qty > 1
            group_key = products[0]["name"] if products else "Diger_Urunler"

            orders_on_page.append({
                "order_no": order_no,
                "recipient": recipient,
                "address": address,
                "cargo_barcode": cargo_bc,
                "products": products,
                "is_multi": is_multi,
                "group_key": group_key
            })

    return orders_on_page

def build_grouped_a4_pdf(grouped_receipts, output_pdf_path, tasarruf_modu):
    doc = SimpleDocTemplate(
        output_pdf_path,
        pagesize=A4,
        leftMargin=12, rightMargin=12, topMargin=8, bottomMargin=8
    )

    styles = getSampleStyleSheet()
    recipient_style = ParagraphStyle('RecipientTitle', parent=styles['Normal'], fontSize=8.5, leading=10.5, fontName=BOLD_FONT, textColor=colors.HexColor("#2C3E50"))
    address_style = ParagraphStyle('AddressText', parent=styles['Normal'], fontSize=6.5, leading=8.5, fontName=MAIN_FONT, textColor=colors.HexColor("#566573"))
    order_style = ParagraphStyle('OrderTitle', parent=styles['Normal'], fontSize=7.0, leading=9.0, fontName=MAIN_FONT, textColor=colors.HexColor("#7F8C8D"))
    multi_style = ParagraphStyle('MultiWarning', parent=styles['Normal'], fontSize=8.0, leading=10, fontName=BOLD_FONT, textColor=colors.HexColor("#FFFFFF"), backColor=colors.HexColor("#C0392B"), alignment=1)
    text_style = ParagraphStyle('LabelText', parent=styles['Normal'], fontSize=6.8, leading=9.0, fontName=MAIN_FONT, textColor=colors.HexColor("#34495E"))
    bc_text_style = ParagraphStyle('BarcodeText', parent=styles['Normal'], fontSize=8.5, leading=11, fontName=BOLD_FONT, alignment=1, textColor=colors.HexColor("#2C3E50"))
    page_header_style = ParagraphStyle('PageHeaderTitle', parent=styles['Normal'], fontSize=8.0, leading=10, fontName=BOLD_FONT, textColor=colors.HexColor("#1B4F72"), alignment=0)

    story = []
    
    if tasarruf_modu:
        all_label_cells = []
        for group_key, items in grouped_receipts.items():
            for item in items:
                cell_elements = []
                cell_elements.append(Paragraph(f"<b>ALICI:</b> {item['recipient']}", recipient_style))
                cell_elements.append(Paragraph(f"<b>ADRES:</b> {item['address']}", address_style))
                cell_elements.append(Paragraph(f"Siparis No: {item['order_no']}", order_style))
                cell_elements.append(Spacer(1, 2))
                
                if item['cargo_barcode'] != "N/A":
                    try:
                        bc = code128.Code128(item['cargo_barcode'], barHeight=42, barWidth=1.1)
                        cell_elements.append(bc)
                        cell_elements.append(Paragraph(f"{item['cargo_barcode']}", bc_text_style))
                    except Exception:
                        cell_elements.append(Paragraph(f"Kargo Kodu: {item['cargo_barcode']}", bc_text_style))
                else:
                    cell_elements.append(Paragraph("<b>[Kargo Barkodu Okunamadı]</b>", recipient_style))

                cell_elements.append(Spacer(1, 2))
                if item["is_multi"]:
                    cell_elements.append(Paragraph("<b>⚠️ DIKKAT: COKLU SIPARIS ⚠️</b>", multi_style))
                    cell_elements.append(Spacer(1, 2))
                
                products_html = ""
                for p in item["products"]:
                    name = p['name']
                    if len(name) > 80:
                        name = name[:77] + "..."
                    products_html += f"<font color='#2980B9'><b>• Urun #{p['index']}:</b></font> {name}<br/>"
                    products_html += f"&nbsp;&nbsp;<b>Adet:</b> <font color='#C0392B'><b>{p['qty']}</b></font> | <b>Renk:</b> {p['color']} | <b>Beden:</b> {p['size']}<br/>"
                    products_html += f"&nbsp;&nbsp;<b>Barkod:</b> {p['barcode']} | <b>Stok Kodu:</b> {p['sku']}<br/><br/>"

                cell_elements.append(Paragraph(products_html.strip(), text_style))
                all_label_cells.append(cell_elements)

        for i in range(0, len(all_label_cells), 6):
            page_chunk = all_label_cells[i:i+6]
            while len(page_chunk) < 6:
                page_chunk.append([Paragraph("", text_style)])

            grid_matrix = [
                [page_chunk[0], page_chunk[1]],
                [page_chunk[2], page_chunk[3]],
                [page_chunk[4], page_chunk[5]]
            ]

            table = Table(grid_matrix, colWidths=[285, 285], rowHeights=[262, 262, 262])
            table.setStyle(TableStyle([
                ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor("#34495E")),
                ('INNERGRID', (0,0), (-1,-1), 0.6, colors.HexColor("#BDC3C7")),
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFFFFF")),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ]))

            story.append(table)
            if i + 6 < len(all_label_cells):
                story.append(PageBreak())
    else:
        total_groups = len(grouped_receipts)
        g_counter = 0

        for group_key, items in grouped_receipts.items():
            g_counter += 1
            label_cells = []

            for item in items:
                cell_elements = []
                cell_elements.append(Paragraph(f"<b>ALICI:</b> {item['recipient']}", recipient_style))
                cell_elements.append(Paragraph(f"<b>ADRES:</b> {item['address']}", address_style))
                cell_elements.append(Paragraph(f"Siparis No: {item['order_no']}", order_style))
                cell_elements.append(Spacer(1, 2))
                
                if item['cargo_barcode'] != "N/A":
                    try:
                        bc = code128.Code128(item['cargo_barcode'], barHeight=42, barWidth=1.1)
                        cell_elements.append(bc)
                        cell_elements.append(Paragraph(f"{item['cargo_barcode']}", bc_text_style))
                    except Exception:
                        cell_elements.append(Paragraph(f"Kargo Kodu: {item['cargo_barcode']}", bc_text_style))
                else:
                    cell_elements.append(Paragraph("<b>[Kargo Barkodu Okunamadı]</b>", recipient_style))

                cell_elements.append(Spacer(1, 2))
                if item["is_multi"]:
                    cell_elements.append(Paragraph("<b>⚠️ DIKKAT: COKLU SIPARIS ⚠️</b>", multi_style))
                    cell_elements.append(Spacer(1, 2))
                
                products_html = ""
                for p in item["products"]:
                    name = p['name']
                    if len(name) > 80:
                        name = name[:77] + "..."
                    products_html += f"<font color='#2980B9'><b>• Urun #{p['index']}:</b></font> {name}<br/>"
                    products_html += f"&nbsp;&nbsp;<b>Adet:</b> <font color='#C0392B'><b>{p['qty']}</b></font> | <b>Renk:</b> {p['color']} | <b>Beden:</b> {p['size']}<br/>"
                    products_html += f"&nbsp;&nbsp;<b>Barkod:</b> {p['barcode']} | <b>Stok Kodu:</b> {p['sku']}<br/><br/>"

                cell_elements.append(Paragraph(products_html.strip(), text_style))
                label_cells.append(cell_elements)

            for i in range(0, len(label_cells), 6):
                page_chunk = label_cells[i:i+6]
                while len(page_chunk) < 6:
                    page_chunk.append([Paragraph("", text_style)])

                display_group_name = group_key if len(group_key) <= 90 else group_key[:87] + "..."
                story.append(Paragraph(f"<b>Urun Grubu:</b> {display_group_name}", page_header_style))
                story.append(Spacer(1, 3))

                grid_matrix = [
                    [page_chunk[0], page_chunk[1]],
                    [page_chunk[2], page_chunk[3]],
                    [page_chunk[4], page_chunk[5]]
                ]

                table = Table(grid_matrix, colWidths=[285, 285], rowHeights=[250, 250, 250])
                table.setStyle(TableStyle([
                    ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor("#34495E")),
                    ('INNERGRID', (0,0), (-1,-1), 0.6, colors.HexColor("#BDC3C7")),
                    ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFFFFF")),
                    ('VALIGN', (0,0), (-1,-1), 'TOP'),
                    ('TOPPADDING', (0,0), (-1,-1), 4),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                    ('LEFTPADDING', (0,0), (-1,-1), 6),
                    ('RIGHTPADDING', (0,0), (-1,-1), 6),
                ]))

                story.append(table)
                if i + 6 < len(label_cells) or g_counter < total_groups:
                    story.append(PageBreak())

    doc.build(story)

# Streamlit Arayüzü
uploaded_files = st.file_uploader("Trendyol Fiş/Etiket PDF'lerini Seçin", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("Etiketleri Dönüştür ve Hazırla"):
        with st.spinner("PDF'ler işleniyor, lütfen bekleyin..."):
            all_orders = []
            
            with tempfile.TemporaryDirectory() as temp_dir:
                for uploaded_file in uploaded_files:
                    temp_path = os.path.join(temp_dir, uploaded_file.name)
                    with open(temp_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    
                    orders = extract_order_data_from_pdf(temp_path)
                    all_orders.extend(orders)

                grouped = defaultdict(list)
                for ord_item in all_orders:
                    grouped[ord_item["group_key"]].append(ord_item)

                output_pdf_path = os.path.join(temp_dir, "Baski_Hazir_Etiketler.pdf")
                if grouped:
                    build_grouped_a4_pdf(grouped, output_pdf_path, sayfa_tasarruf)
                    
                    with open(output_pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    
                    st.success("İşlem Başarıyla Tamamlandı! 🎉")
                    st.download_button(
                        label="📥 Düzenlenmiş PDF Çıktısını İndir",
                        data=pdf_bytes,
                        file_name="adsbarkod.pdf",
                        mime="application/pdf"
                    )
                else:
                    st.warning("Yüklenen dosyalarda uygun sipariş verisi bulunamadı.")
