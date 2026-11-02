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
st.write("Trendyol'dan indirdiğiniz PDF fişlerini buraya yükleyin, sistem bunları otomatik gruplayarak A4 formatında tek bir şık PDF'e dönüştürsün.")

# Font tanımlamaları (Bulut sunucularda Helvetica yedekli çalışır)
MAIN_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

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
                recipient = re.sub(r'^[:\s]+', '', r_match.group(1)).strip().replace('\n', ' ')

            # 3. Kargo Barkodu
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

            # 4. Ürün Bilgileri
            products = []
            if "Ürün Bilgileri" in text:
                prod_section = text.split("Ürün Bilgileri", 1)[1].strip()
                header_pattern = r'Adet\s+Renk\s+Beden\s+Barkod\s+Stok\s+Kodu'
                current_working_text = prod_section
                product_counter = 1

                while True:
                    if not re.search(header_pattern, current_working_text, re.IGNORECASE):
                        break

                    parts = re.split(header_pattern, current_working_text, flags=re.IGNORECASE, maxsplit=1)
                    if len(parts) < 2:
                        break

                    product_name = parts[0].strip()
                    product_name = re.sub(r'^\d+\s*', '', product_name).strip()

                    after_header = parts[1].strip()
                    lines_after = [l.strip() for l in after_header.split('\n') if l.strip()]

                    if not lines_after:
                        break

                    val_line = lines_after[0]
                    tokens = [t for t in val_line.split() if t != "-1"]

                    qty, color, size, barcode, sku = "-", "-", "-", "-", "-"
                    
                    if len(tokens) >= 2 and "adet" in tokens[1].lower():
                        qty = f"{tokens[0]} {tokens[1]}"
                        tokens = tokens[2:]
                    elif len(tokens) >= 1 and "adet" in tokens[0].lower():
                        qty = tokens[0]
                        tokens = tokens[1:]

                    if tokens:
                        sku = tokens[-1]
                        if len(tokens) > 1:
                            barcode = tokens[-2]
                        middle = tokens[:-2]
                        if middle:
                            color = middle[0]
                            if len(middle) > 1:
                                size = " ".join(middle[1:])
                            else:
                                size = "Tek Ebat"
                        else:
                            size = "Tek Ebat"

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
                    current_working_text = "\n".join(lines_after[1:])

            total_qty = 0
            for p in products:
                qm = re.search(r'\d+', p["qty"])
                if qm: total_qty += int(qm.group(0))
            
            is_multi = len(products) > 1 or total_qty > 1
            group_key = products[0]["barcode"] if products and products[0]["barcode"] != "-" else cargo_bc

            orders_on_page.append({
                "order_no": order_no,
                "recipient": recipient,
                "cargo_barcode": cargo_bc,
                "products": products,
                "is_multi": is_multi,
                "group_key": group_key
            })

    return orders_on_page

def build_grouped_a4_pdf(grouped_receipts, output_pdf_path):
    doc = SimpleDocTemplate(
        output_pdf_path,
        pagesize=A4,
        leftMargin=12, rightMargin=12, topMargin=8, bottomMargin=8
    )

    styles = getSampleStyleSheet()
    recipient_style = ParagraphStyle('RecipientTitle', parent=styles['Normal'], fontSize=9.5, leading=12, fontName=BOLD_FONT, textColor=colors.HexColor("#2C3E50"))
    order_style = ParagraphStyle('OrderTitle', parent=styles['Normal'], fontSize=7.5, leading=9.5, fontName=MAIN_FONT, textColor=colors.HexColor("#7F8C8D"))
    multi_style = ParagraphStyle('MultiWarning', parent=styles['Normal'], fontSize=8.5, leading=11, fontName=BOLD_FONT, textColor=colors.HexColor("#FFFFFF"), backColor=colors.HexColor("#C0392B"), alignment=1)
    text_style = ParagraphStyle('LabelText', parent=styles['Normal'], fontSize=7.5, leading=10, fontName=MAIN_FONT, textColor=colors.HexColor("#34495E"))
    bc_text_style = ParagraphStyle('BarcodeText', parent=styles['Normal'], fontSize=9.5, leading=12, fontName=BOLD_FONT, alignment=1, textColor=colors.HexColor("#2C3E50"))

    story = []
    total_groups = len(grouped_receipts)
    g_counter = 0

    for group_key, items in grouped_receipts.items():
        g_counter += 1
        label_cells = []

        for item in items:
            cell_elements = []
            cell_elements.append(Paragraph(f"<b>ALICI:</b> {item['recipient']}", recipient_style))
            cell_elements.append(Paragraph(f"Sipariş No: {item['order_no']}", order_style))
            cell_elements.append(Spacer(1, 3))
            
            if item['cargo_barcode'] != "N/A":
                try:
                    bc = code128.Code128(item['cargo_barcode'], barHeight=34, barWidth=0.92)
                    cell_elements.append(bc)
                    cell_elements.append(Paragraph(f"{item['cargo_barcode']}", bc_text_style))
                except Exception:
                    cell_elements.append(Paragraph(f"Kargo Kodu: {item['cargo_barcode']}", bc_text_style))
            else:
                cell_elements.append(Paragraph("<b>[Kargo Barkodu Okunamadı]</b>", recipient_style))

            cell_elements.append(Spacer(1, 3))
            if item["is_multi"]:
                cell_elements.append(Paragraph("<b>⚠️ DİKKAT: ÇOKLU SİPARİŞ ⚠️</b>", multi_style))
                cell_elements.append(Spacer(1, 3))
            
            products_html = ""
            for p in item["products"]:
                name = p['name']
                if len(name) > 95:
                    name = name[:92] + "..."
                products_html += f"<font color='#2980B9'><b>• Ürün #{p['index']}:</b></font> {name}<br/>"
                products_html += f"&nbsp;&nbsp;<b>Adet:</b> <font color='#C0392B'><b>{p['qty']}</b></font> | <b>Renk:</b> {p['color']} | <b>Beden:</b> {p['size']}<br/>"
                products_html += f"&nbsp;&nbsp;<b>Barkod:</b> {p['barcode']} | <b>Stok Kodu:</b> {p['sku']}<br/><br/>"

            cell_elements.append(Paragraph(products_html.strip(), text_style))
            label_cells.append(cell_elements)

        for i in range(0, len(label_cells), 6):
            page_chunk = label_cells[i:i+6]
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
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
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
                    build_grouped_a4_pdf(grouped, output_pdf_path)
                    
                    with open(output_pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    
                    st.success("İşlem Başarıyla Tamamlandı! 🎉")
                    st.download_button(
                        label="📥 Düzenlenmiş PDF Çıktısını İndir",
                        data=pdf_bytes,
                        file_name="Stil_Sahibi_Baski_Etiketler.pdf",
                        mime="application/pdf"
                    )
                else:
                    st.warning("Yüklenen dosyalarda uygun sipariş verisi bulunamadı.")
