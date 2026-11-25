import io
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from ai_reader import read_source, ask_ollama, normalize
import streamlit as st
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

st.set_page_config(page_title='U&I | Báo giá hàng xuất', page_icon='🚢', layout='wide')
NAVY=colors.HexColor('#214C7C'); BLUE=colors.HexColor('#325C98'); LIGHT=colors.HexColor('#EAF0F7'); YELLOW=colors.HexColor('#FFE18A')

@st.cache_resource
def fonts():
    paths=[(Path(__file__).parent/'fonts'/'DejaVuSans.ttf',Path(__file__).parent/'fonts'/'DejaVuSans-Bold.ttf'),
           (Path('C:/Windows/Fonts/arial.ttf'),Path('C:/Windows/Fonts/arialbd.ttf')),
           (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))]
    for regular,bold in paths:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont('UI-Regular',str(regular)))
            pdfmetrics.registerFont(TTFont('UI-Bold',str(bold)))
            return 'UI-Regular','UI-Bold'
    return 'Helvetica','Helvetica-Bold'

FONT,BOLD=fonts()

INITIAL=[
 {'Nhóm phí':'OCEAN FREIGHT','Khoản phí':'Ocean Freight HCM - Incheon','Đơn vị':'CONT','Số lượng':1.0,'Giá mua (USD)':300.0,'Giá bán (USD)':430.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'THC','Đơn vị':'CONT','Số lượng':1.0,'Giá mua (USD)':120.0,'Giá bán (USD)':120.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'LSS','Đơn vị':'CONT','Số lượng':1.0,'Giá mua (USD)':130.0,'Giá bán (USD)':150.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'Seal','Đơn vị':'CONT','Số lượng':1.0,'Giá mua (USD)':10.0,'Giá bán (USD)':9.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'Telex Release Fee','Đơn vị':'HBL','Số lượng':1.0,'Giá mua (USD)':30.0,'Giá bán (USD)':30.0,'Ghi chú':'If required','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'Docs Fee','Đơn vị':'HBL','Số lượng':1.0,'Giá mua (USD)':45.0,'Giá bán (USD)':38.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'ORIGIN LOCAL CHARGES','Khoản phí':'Handling Fee','Đơn vị':'HBL','Số lượng':1.0,'Giá mua (USD)':0.0,'Giá bán (USD)':20.0,'Ghi chú':'','Hiển thị PDF':True},
 {'Nhóm phí':'CHI PHÍ NỘI BỘ','Khoản phí':'Commission (internal)','Đơn vị':'JOB','Số lượng':1.0,'Giá mua (USD)':10.0,'Giá bán (USD)':0.0,'Ghi chú':'Internal only','Hiển thị PDF':False},
]
if 'fees' not in st.session_state: st.session_state.fees=pd.DataFrame(INITIAL)
if 'editor_version' not in st.session_state: st.session_state.editor_version=0

st.title('🚢 PHẦN MỀM BÁO GIÁ HÀNG XUẤT U&I')
st.caption('Bản thử nghiệm 3.0 · Sea Freight FCL/FCL · AI đọc bảng giá bằng Ollama chạy trên máy · Sales phải duyệt dữ liệu')
with st.expander('1. THÔNG TIN LÔ HÀNG XUẤT',expanded=True):
    a,b,c=st.columns(3)
    with a:
        customer=st.text_input('Khách hàng',value='Valued Customer')
        pol=st.text_input('Cảng xuất (POL)',value='Ho Chi Minh Port, Vietnam')
        incoterm=st.selectbox('Incoterms',['EXW','FCA','FAS','FOB','CFR','CIF','CPT','CIP','DAP','DPU','DDP'],index=3)
        container=st.selectbox('Loại container',['20GP','40GP','40HC','20RF','40RF'])
    with b:
        pod=st.text_input('Cảng đến (POD)',value='Incheon Port, Korea')
        commodity=st.text_input('Hàng hóa',value='General Cargo')
        qty_container=st.number_input('Số container',min_value=1,value=1,step=1)
        carrier=st.text_input('Hãng tàu (nếu đã xác nhận)',value='')
    with c:
        quote_date=st.date_input('Ngày báo giá',value=date.today())
        valid_until=st.date_input('Hiệu lực đến',value=date.today()+timedelta(days=7))
        quote_id=st.text_input('Mã báo giá',value='DEMO-EXPORT-001')
        sales_name=st.text_input('Nhân viên Sales',value='')
        sales_email=st.text_input('Email Sales',value='')
        sales_phone=st.text_input('Điện thoại Sales',value='')
    st.info(f'Lô hàng xuất: {pol} → {pod} | {qty_container} × {container} | {incoterm}. Incoterms chỉ hỗ trợ kiểm tra phạm vi dịch vụ, không tự sinh khoản phí.')

st.subheader('2. AI ĐỌC BẢNG GIÁ PRICING (CHẠY CỤC BỘ)')
st.caption('Hỗ trợ Excel/CSV, PDF có chữ, ảnh và PDF scan. Không gửi tài liệu lên dịch vụ AI trả phí. Cần cài Ollama và tải mô hình phù hợp.')
with st.expander('Tải bảng giá và trích xuất bằng AI', expanded=False):
    uploaded = st.file_uploader('Bảng giá Pricing / Agent', type=['xlsx','xls','csv','pdf','txt','png','jpg','jpeg','webp'])
    model = st.text_input('Tên mô hình Ollama',value='qwen2.5:3b',help='Cho ảnh/PDF scan: tải mô hình thị giác, ví dụ qwen2.5vl:3b (nếu máy hỗ trợ).')
    st.info('AI KHÔNG tự xác nhận hướng xuất/nhập, Incoterms, số lượng, tỷ giá hay phí phát sinh. Mọi dòng trích xuất mặc định ẨN trên PDF và giá bán tạm bằng giá mua để Sales điều chỉnh.')
    if st.button('🤖 AI ĐỌC BẢNG GIÁ',disabled=uploaded is None):
        try:
            source_text,source_images,source_warnings=read_source(uploaded.name,uploaded.getvalue())
            if source_images and not any(token in model.lower() for token in ('vl','vision','llava','gemma3')):
                st.error('Tệp ảnh/PDF scan cần mô hình AI thị giác. Hãy chọn qwen2.5vl:3b (sau khi tải bằng Ollama).')
            else:
                with st.spinner('AI đang đọc tài liệu trên máy tính...'):
                    result=ask_ollama(source_text,source_images,model=model)
                candidate,warnings=normalize(result)
                st.session_state.ai_candidate=candidate
                st.session_state.ai_metadata={k:result.get(k,'') for k in ('direction','pol','pod','incoterm','valid_until','currency')}
                st.session_state.ai_warnings=source_warnings+warnings
        except Exception as exc:
            st.error(f'Không thể đọc bảng giá: {exc}')
    if 'ai_candidate' in st.session_state:
        st.markdown('**Thông tin AI nhận diện (chỉ để kiểm tra, chưa tự thay đổi lô hàng):**')
        st.json(st.session_state.ai_metadata)
        for warning in st.session_state.ai_warnings: st.warning(warning)
        st.markdown('**Bảng phí đề xuất — Sales kiểm tra, sửa giá bán, số lượng, nhóm phí và đánh dấu hiển thị:**')
        st.session_state.ai_candidate=st.data_editor(st.session_state.ai_candidate,num_rows='dynamic',hide_index=True,width='stretch',key='ai_review',column_config={
            'Nhóm phí':st.column_config.SelectboxColumn(options=GROUPS),
            'Hiển thị PDF':st.column_config.CheckboxColumn(),
            'Giá mua (USD)':st.column_config.NumberColumn(min_value=0),
            'Giá bán (USD)':st.column_config.NumberColumn(min_value=0),
            'Số lượng':st.column_config.NumberColumn(min_value=0),
        })
        if st.button('✅ XÁC NHẬN VÀ ĐƯA CÁC DÒNG ĐÃ KIỂM TRA VÀO BẢNG NỘI BỘ'):
            new=st.session_state.ai_candidate.copy()
            if new.empty:
                st.error('Không có dòng phí hợp lệ.')
            else:
                st.session_state.fees=pd.concat([st.session_state.fees,new],ignore_index=True)
                st.session_state.editor_version+=1
                del st.session_state.ai_candidate
                st.success('Đã thêm dữ liệu AI vào bảng nội bộ. Hãy kiểm tra các dòng mới và giá bán trước khi xuất PDF.')
                st.rerun()

st.subheader('2. BẢNG CHI PHÍ NỘI BỘ — CHỈ SALES XEM')
st.caption('Chỉnh giá mua, giá bán, số lượng và nhóm phí trực tiếp. Đánh dấu “Hiển thị PDF” cho phí được phép gửi khách. Phí nội bộ luôn bị loại khỏi PDF.')
GROUPS=['OCEAN FREIGHT','ORIGIN LOCAL CHARGES','DESTINATION LOCAL CHARGES','CHI PHÍ NỘI BỘ']
fees=st.data_editor(st.session_state.fees,num_rows='dynamic',hide_index=True,width='stretch',key=f'editor_{st.session_state.editor_version}',column_config={
 'Nhóm phí':st.column_config.SelectboxColumn('Nhóm phí',options=GROUPS,required=True),
 'Số lượng':st.column_config.NumberColumn('Số lượng',min_value=0,format='%.3f'),
 'Giá mua (USD)':st.column_config.NumberColumn('Giá mua (USD)',min_value=0,format='%.2f'),
 'Giá bán (USD)':st.column_config.NumberColumn('Giá bán (USD)',min_value=0,format='%.2f'),
 'Hiển thị PDF':st.column_config.CheckboxColumn('Hiển thị PDF')
})
fees=fees.copy()
st.session_state.fees=fees.copy()
for field in ['Số lượng','Giá mua (USD)','Giá bán (USD)']:
    fees[field]=pd.to_numeric(fees[field],errors='coerce').fillna(0)
for field in ['Nhóm phí','Khoản phí','Đơn vị','Ghi chú']:
    fees[field]=fees[field].fillna('').astype(str)
fees['Thành tiền mua']=fees['Số lượng']*fees['Giá mua (USD)']
fees['Thành tiền bán']=fees['Số lượng']*fees['Giá bán (USD)']
fees['Lợi nhuận']=fees['Thành tiền bán']-fees['Thành tiền mua']
internal=fees['Nhóm phí'].eq('CHI PHÍ NỘI BỘ') | fees['Khoản phí'].str.contains('commission|internal',case=False,regex=True)
public=fees[fees['Hiển thị PDF'].fillna(False).astype(bool) & ~internal & fees['Khoản phí'].str.strip().ne('')].copy()
all_cost=float(fees['Thành tiền mua'].sum())
all_sell=float(public['Thành tiền bán'].sum())
profit=all_sell-all_cost
margin=profit/all_sell*100 if all_sell>0 else 0
x,y,z,w=st.columns(4)
x.metric('Giá vốn nội bộ',f'{all_cost:,.2f} USD')
y.metric('Tổng giá bán khách hàng',f'{all_sell:,.2f} USD')
z.metric('Lợi nhuận dự kiến',f'{profit:,.2f} USD')
w.metric('Tỷ suất LN / giá bán',f'{margin:.2f}%')
with st.expander('Xem bảng tính lợi nhuận từng khoản (nội bộ)'):
    st.dataframe(fees[['Nhóm phí','Khoản phí','Thành tiền mua','Thành tiền bán','Lợi nhuận']],hide_index=True,width='stretch')

st.subheader('3. THÔNG TIN BÁO GIÁ KHÁCH HÀNG')
notes=st.text_area('Ghi chú gửi khách hàng (mỗi dòng là một ghi chú)',value='Rates are subject to carrier confirmation and validity.\nVAT, customs duty, inspection, storage, Dem/Det and other charges are excluded unless expressly quoted.\nAdditional services and unforeseen charges are subject to prior confirmation.',height=115)
service_scope=st.text_input('Phạm vi dịch vụ (nếu cần)',value='Port-to-Port service, subject to confirmed charges')
st.caption('Các ghi chú trên chỉ là ví dụ để kiểm tra bố cục. Sales phải sửa theo từng lô hàng trước khi gửi khách.')

styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='UI',fontName=FONT,fontSize=8.2,leading=12,textColor=colors.HexColor('#222222')))
styles.add(ParagraphStyle(name='UIBold',parent=styles['UI'],fontName=BOLD))
styles.add(ParagraphStyle(name='UIWhite',parent=styles['UI'],fontName=BOLD,textColor=colors.white,fontSize=7.2,leading=10))
styles.add(ParagraphStyle(name='UITitle',fontName=BOLD,fontSize=16,leading=22,alignment=TA_CENTER,textColor=NAVY))
styles.add(ParagraphStyle(name='UISmall',parent=styles['UI'],fontSize=7,leading=10))

def P(value,style='UI'):
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(value)).replace('\n','<br/>'),styles[style])

def create_pdf(rows):
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=(210*mm,297*mm),leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=14*mm,title='Ocean Export Quotation',author='U&I Logistics - Demo')
    story=[P('U&I LOGISTICS CORPORATION','UITitle'),Spacer(1,2*mm),P('OCEAN EXPORT QUOTATION','UITitle'),Spacer(1,6*mm)]
    info=[('To',customer),('Dated',quote_date.strftime('%d-%b-%Y')),('Quotation No.',quote_id),('POL',pol),('POD',pod),('Commodity',commodity),('Valid to',valid_until.strftime('%d-%b-%Y')),('Mode',f'{incoterm} | Sea FCL/FCL | {qty_container} × {container}'),('Carrier',carrier or 'To be confirmed'),('Service',service_scope)]
    it=Table([[P(k+':','UIBold'),P(v)] for k,v in info],colWidths=[34*mm,150*mm],hAlign='LEFT')
    it.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),2)]))
    story.extend([it,Spacer(1,6*mm)])
    headings=['JOB','DESCRIPTION','PRICE (USD)','UNIT','QTY','AMOUNT (USD)','CARRIER / NOTE']
    table_rows=[[P(h,'UIWhite') for h in headings]]
    for group in ['OCEAN FREIGHT','ORIGIN LOCAL CHARGES','DESTINATION LOCAL CHARGES']:
        subset=rows[rows['Nhóm phí'].eq(group)]
        for i,(_,r) in enumerate(subset.iterrows()):
            table_rows.append([P(group if i==0 else '', 'UIBold'),P(r['Khoản phí']),P(f"{r['Giá bán (USD)']:,.2f}"),P(r['Đơn vị']),P(f"{r['Số lượng']:g}"),P(f"{r['Thành tiền bán']:,.2f}"),P((carrier+'; ' if group=='OCEAN FREIGHT' and carrier else '')+r['Ghi chú'])])
    table_rows.append([P('TOTAL ESTIMATED CHARGES (USD)','UIBold'),'','','','',P(f'{all_sell:,.2f}','UIBold'),''])
    widths=[29*mm,43*mm,22*mm,15*mm,12*mm,25*mm,38*mm]
    tbl=Table(table_rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
    commands=[('BACKGROUND',(0,0),(-1,0),NAVY),('GRID',(0,0),(-1,-1),0.45,colors.HexColor('#777777')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('BACKGROUND',(0,-1),(-1,-1),YELLOW),('SPAN',(0,-1),(4,-1)),('ALIGN',(2,1),(5,-1),'RIGHT')]
    tbl.setStyle(TableStyle(commands))
    story.extend([tbl,Spacer(1,6*mm),P('Remarks:','UIBold')])
    for line in notes.splitlines():
        if line.strip(): story.append(P('• '+line.strip(),'UISmall'))
    story.extend([Spacer(1,7*mm),P('Thank you for giving U&I Logistics the opportunity to quote on your business.'),P('Best regards,'),Spacer(1,2*mm),P('U&I LOGISTICS CORPORATION','UIBold')])
    if sales_name.strip(): story.append(P('Sales: '+sales_name))
    if sales_phone.strip(): story.append(P('Phone: '+sales_phone))
    if sales_email.strip(): story.append(P('Email: '+sales_email))
    story.append(P('Web: www.unilogistics.vn'))
    def footer(canvas,document):
        canvas.setFont(FONT,7);canvas.setFillColor(colors.grey)
        canvas.drawRightString(197*mm,8*mm,f'Page {document.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buf.getvalue()

st.subheader('4. XEM TRƯỚC & XUẤT PDF')
st.caption('Bảng dưới đây chỉ chứa các trường được phép đưa vào PDF khách hàng.')
if not public.empty:
    st.dataframe(public[['Nhóm phí','Khoản phí','Đơn vị','Số lượng','Giá bán (USD)','Thành tiền bán','Ghi chú']],hide_index=True,width='stretch')
errors=[]
if not customer.strip() or not pol.strip() or not pod.strip(): errors.append('Thiếu khách hàng hoặc POL/POD.')
if valid_until < quote_date: errors.append('Ngày hết hiệu lực sớm hơn ngày báo giá.')
if public.empty: errors.append('Chưa có khoản phí công khai.')
if public['Giá bán (USD)'].lt(0).any() or public['Số lượng'].le(0).any(): errors.append('Đơn giá không được âm và số lượng phải lớn hơn 0.')
if fees.loc[internal,'Hiển thị PDF'].fillna(False).astype(bool).any(): st.warning('Một số phí nội bộ đang được đánh dấu hiển thị; hệ thống vẫn tự loại các phí này khỏi PDF.')
if errors:
    for err in errors: st.error(err)
else:
    try:
        data=create_pdf(public)
        st.download_button('📄 TẢI BÁO GIÁ PDF CHO KHÁCH HÀNG',data=data,file_name=f'Export_Quotation_{quote_id.replace("/","-")}.pdf',mime='application/pdf',type='primary')
        st.success('PDF chỉ chứa giá bán và các thông tin công khai. Giá mua, profit và commission không được đưa vào dữ liệu PDF.')
    except Exception as e:
        st.error(f'Không thể tạo PDF: {e}')
st.caption('Bản demo học thuật. AI đọc tài liệu cục bộ có thể sai; Sales cần đối chiếu bảng gốc, xác nhận hiệu lực, đơn vị, tỷ giá và giá bán trước khi gửi khách.')
