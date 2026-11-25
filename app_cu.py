import base64
import hashlib
from html import escape
import io
import json
import re
import time
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as PDFImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import app_config
import security_store
from ai_reader import (
    ask_openai_compatible,
    ask_ollama,
    normalize,
    normalize_quote_metadata,
    read_source,
)
from pricing_logic import (
    FEE_COLUMNS,
    GROUPS,
    allocate_profit_by_cost,
    calculate_quote,
    internal_fee_mask,
    validate_pdf,
)

LOGO_PATH = Path(__file__).parent / "assets" / "ui_logo.png"
LOGO_DATA_URI = (
    "data:image/png;base64,"
    + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
)
DASHBOARD_ART_DATA_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(
        (Path(__file__).parent / "assets" / "dashboard_logistics.svg").read_bytes()
    ).decode("ascii")
)
DASHBOARD_SIDEBAR_DATA_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(
        (Path(__file__).parent / "assets" / "dashboard_sidebar.svg").read_bytes()
    ).decode("ascii")
)

st.set_page_config(
    page_title="U&I | Báo giá hàng xuất",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

NAVY = colors.HexColor("#006494")
BLUE = colors.HexColor("#006494")
LIGHT_BLUE = colors.HexColor("#EAF4FA")
GOLD = colors.HexColor("#F2C66D")
PUBLIC_GROUPS = [group for group in GROUPS if group != "CHI PHÍ NỘI BỘ"]
PDF_GROUPS = [
    group for group in PUBLIC_GROUPS if group != "CHƯA PHÂN LOẠI"
]
SESSION_IDLE_TIMEOUT = 30 * 60
QUOTE_FIELDS = {
    "quote_customer": "",
    "quote_pol": "",
    "quote_pod": "",
    "quote_incoterm": "",
    "quote_container": "",
    "quote_qty": None,
    "quote_commodity": "",
    "quote_carrier": "",
    "quote_date": date.today(),
    "quote_valid_until": None,
    "quote_number": "UI-" + date.today().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper(),
    "quote_sales_name": "",
    "quote_sales_email": "",
    "quote_sales_phone": "",
    "quote_notes": (
        "Rates are subject to carrier confirmation and validity.\n"
        "VAT, customs duty, inspection, storage, Dem/Det and other charges "
        "are excluded unless expressly quoted.\n"
        "Additional services and unforeseen charges are subject to prior "
        "confirmation."
    ),
    "quote_service_scope": "Port-to-Port service, subject to confirmed charges",
}


def _new_quote_defaults():
    defaults = QUOTE_FIELDS.copy()
    today = date.today()
    defaults["quote_date"] = today
    defaults["quote_valid_until"] = None
    defaults["quote_number"] = (
        "UI-" + today.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    )
    return defaults


def apply_styles():
    st.markdown(
        """
        <style>
        :root {
            --ui-navy: #00516f;
            --ui-blue: #006494;
            --ui-light: #eaf4f8;
            --ui-pastel: #d9ebf2;
        }
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #edf6fa 0%, #f8fbfc 420px);
        }
        [data-testid="stSidebar"] {
            background: #f0f7fa;
            border-right: 1px solid #cfe0e8;
        }
        .block-container {
            max-width: 1440px;
            padding-top: 1.7rem;
            padding-bottom: 3rem;
        }
        h1, h2, h3 { color: var(--ui-navy); }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #dce8f0;
            border-radius: 14px;
            padding: 14px 16px;
            box-shadow: 0 3px 12px rgba(18, 59, 93, 0.05);
        }
        [data-testid="stMetricLabel"] { color: #527187; }
        [data-testid="stBaseButton-primary"],
        [data-testid="stBaseButton-primaryFormSubmit"],
        [data-testid="stDownloadButton"] button {
            background: var(--ui-blue);
            border-color: var(--ui-blue);
            border-radius: 9px;
            font-weight: 650;
            transition: background-color .15s ease, box-shadow .15s ease;
        }
        [data-testid="stBaseButton-primary"]:hover,
        [data-testid="stBaseButton-primaryFormSubmit"]:hover,
        [data-testid="stDownloadButton"] button:hover {
            background: #004d70;
            border-color: #004d70;
            box-shadow: 0 4px 12px rgba(0, 100, 148, .22);
        }
        [data-testid="stDataEditor"] {
            border: 1px solid #dce8f0;
            border-radius: 12px;
            overflow: hidden;
        }
        [data-testid="stTabs"] [role="tablist"] {
            gap: .45rem;
            border-bottom: 1px solid #dce8f0;
        }
        [data-testid="stTabs"] [role="tab"] {
            border-radius: 10px 10px 0 0;
            color: #527187;
            font-weight: 650;
            padding: .65rem 1rem;
        }
        [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
            color: var(--ui-blue);
            background: #dcecf3;
        }
        [data-testid="stAlert"] {
            border-radius: 10px;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stNumberInput"] input {
            border-radius: 8px;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] {
            border: 1px solid #d9e8f4;
            border-radius: 10px;
            background: rgba(255, 255, 255, .76);
        }
        [data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 8px;
            padding: .35rem .5rem;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: var(--ui-blue);
            box-shadow: inset 0 0 0 1px #004d70;
            color: #ffffff;
            font-weight: 650;
        }
        [data-testid="stSidebar"] [role="radiogroup"] input[type="radio"] {
            accent-color: var(--ui-blue) !important;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)
        input[type="radio"] {
            accent-color: #ffffff !important;
        }
        [class*="st-key-login-card"] {
            width: 100%;
            max-width: 460px;
            margin: 4vh auto 2vh;
            padding: 2rem 2.1rem;
            background: rgba(255, 255, 255, .97);
            border: 1px solid #dce9f4;
            border-radius: 24px;
            box-shadow: 0 20px 55px rgba(31, 78, 115, .16);
        }
        .ui-login-heading {
            text-align: center;
            margin-bottom: 1.6rem;
        }
        .ui-login-mark {
            display: block;
            width: 112px;
            height: 112px;
            object-fit: contain;
            margin: 0 auto .85rem;
            background: transparent;
        }
        .ui-login-heading h1 {
            margin: 0;
            color: var(--ui-navy);
            font-size: 1.65rem;
        }
        .ui-login-heading p {
            margin: .4rem 0 0;
            color: #6b8398;
            font-size: .92rem;
        }
        .ui-sidebar-brand {
            display: flex;
            align-items: center;
            gap: .75rem;
            padding: .8rem;
            margin-bottom: .8rem;
            border: 1px solid #c9dfe9;
            border-radius: 13px;
            background: #e3f0f5;
        }
        .ui-sidebar-mark {
            display: block;
            width: 54px;
            height: 54px;
            flex: 0 0 54px;
            object-fit: contain;
            padding: 2px;
            border-radius: 10px;
            background: #ffffff;
        }
        .ui-sidebar-title {
            color: var(--ui-navy);
            font-size: .94rem;
            font-weight: 750;
            line-height: 1.25;
        }
        .ui-sidebar-subtitle {
            color: #6d8497;
            font-size: .72rem;
            margin-top: .15rem;
        }
        .ui-sidebar-user {
            padding: .35rem .25rem .7rem;
            color: #526d82;
            font-size: .85rem;
        }
        .ui-hero {
            background: linear-gradient(115deg, #006494, #1682a1);
            border-radius: 18px;
            color: white;
            padding: 22px 26px;
            margin-bottom: 20px;
            box-shadow: 0 8px 24px rgba(0, 100, 148, .18);
        }
        .ui-hero h1 { color: white; margin: 0; font-size: 1.8rem; }
        .ui-hero p { color: #dceef8; margin: 7px 0 0; }
        .ui-eyebrow {
            color: #c9e6f0;
            font-size: .75rem;
            font-weight: 700;
            letter-spacing: .12em;
            text-transform: uppercase;
        }
        .ui-dashboard-subtitle {
            margin-top: -.5rem;
            margin-bottom: 1.25rem;
            color: #5b7488;
            font-size: 1rem;
        }
        .ui-step-card {
            display: flex;
            align-items: center;
            gap: .75rem;
            min-height: 74px;
            padding: .8rem 1rem;
            border: 1px solid #dce8f0;
            border-radius: 12px;
            background: linear-gradient(110deg, #fff, #f1f7ff);
            box-shadow: 0 3px 10px rgba(44, 75, 119, .05);
        }
        .ui-step-icon {
            display: grid;
            flex: 0 0 44px;
            place-items: center;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: #dcecff;
            color: #0876c4;
            font-size: 1.25rem;
        }
        .ui-step-card strong {
            display: block;
            color: var(--ui-navy);
            margin-bottom: .2rem;
        }
        .ui-step-card div > span {
            color: #5b7488;
            font-size: .85rem;
        }
        body:has(.st-key-admin-workspace) [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #eaf3ff 0, #f7f9fd 58%, #eef4fc 100%);
        }
        body:has(.st-key-admin-workspace) .block-container {
            max-width: 1500px;
            padding-top: .55rem;
            padding-bottom: 1.5rem;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] {
            position: relative;
            overflow: hidden;
            background:
                linear-gradient(180deg, rgba(0, 49, 91, .96), rgba(0, 75, 126, .94)),
                url("DASHBOARD_SIDEBAR_PLACEHOLDER") center bottom / 100% auto no-repeat,
                #063d70;
            border-right: 1px solid #0b5b95;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] > div:first-child {
            position: relative;
            z-index: 1;
            background: transparent;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] .ui-sidebar-brand,
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] .ui-sidebar-user {
            border: 0;
            background: transparent;
            color: #e8f4ff;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] .ui-sidebar-title,
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] .ui-sidebar-subtitle,
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #e8f4ff;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [data-testid="stExpander"] {
            border: 0;
            background: transparent;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [data-testid="stExpander"] summary {
            border: 1px solid rgba(151, 207, 246, .22);
            border-radius: 9px;
            color: #fff;
            background: rgba(37, 116, 177, .34);
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [role="radiogroup"] label {
            color: #eaf5ff;
            border-radius: 8px;
            padding: .45rem .55rem;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: linear-gradient(100deg, #0879ce, #138ee0);
            box-shadow: 0 4px 12px rgba(0, 32, 71, .22);
            color: #fff;
        }
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
            color: #fff;
            border-color: rgba(171, 215, 247, .45);
            background: rgba(8, 77, 132, .58);
        }
        body:has(.st-key-admin-workspace) .st-key-admin-topbar {
            padding: .05rem .3rem .45rem;
            border-bottom: 1px solid #d6e2f1;
            margin-bottom: .35rem;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-menu button,
        body:has(.st-key-admin-workspace) .st-key-admin-notifications button,
        body:has(.st-key-admin-workspace) .st-key-admin-settings button,
        body:has(.st-key-admin-workspace) .st-key-admin-profile button {
            min-height: 2.45rem;
            border: 0;
            background: transparent;
            color: #233d60;
            font-size: 1.15rem;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-profile button {
            width: 2.45rem;
            min-width: 2.45rem;
            height: 2.45rem;
            border-radius: 50%;
            background: #075d9d;
            color: #fff;
            font-weight: 750;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-menu button {
            font-size: 1.4rem;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-search [data-testid="stTextInput"] input {
            min-height: 2.45rem;
            padding-left: 2.15rem;
            border-color: #c9d8ec;
            border-radius: 9px;
            background: rgba(255,255,255,.92);
            font-size: .8rem;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-search [data-testid="stTextInput"] {
            position: relative;
        }
        body:has(.st-key-admin-workspace) .st-key-admin-search [data-testid="stTextInput"]::before {
            content: "⌕";
            position: absolute;
            z-index: 2;
            left: .65rem;
            top: .25rem;
            color: #526782;
            font-size: 1.35rem;
        }
        .ui-admin-hero {
            min-height: 124px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            margin: .1rem 0 1rem;
            padding: 1.1rem 1.7rem;
            border: 1px solid rgba(145, 197, 242, .55);
            border-radius: 15px;
            background:
                linear-gradient(90deg, rgba(0, 68, 127, .98) 0%, rgba(0, 105, 176, .84) 38%, rgba(0, 113, 192, .12) 100%),
                url("DASHBOARD_ART_PLACEHOLDER") center 50% / cover no-repeat;
            box-shadow: 0 7px 20px rgba(12, 72, 131, .13);
            color: #fff;
        }
        .ui-admin-hero h1 {
            margin: .2rem 0 0;
            color: #fff;
            font-size: clamp(1.7rem, 3vw, 2.45rem);
            font-weight: 780;
            letter-spacing: -.025em;
        }
        .ui-admin-hero p {
            margin: .35rem 0 0;
            color: #e5f3ff;
            font-size: .95rem;
        }
        .ui-admin-card-title {
            display: flex;
            align-items: center;
            gap: .55rem;
            color: #17365f;
            font-size: 1.06rem;
            font-weight: 750;
        }
        .ui-admin-card-title svg {
            width: 23px;
            height: 23px;
            color: #0876c4;
        }
        .ui-admin-status {
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            padding: .16rem .55rem;
            border-radius: 999px;
            background: #dff4e8;
            color: #176444;
            font-size: .76rem;
        }
        .ui-admin-status::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #20a36c;
        }
        .ui-admin-status-lock {
            background: #fde5e7;
            color: #9b2833;
        }
        .ui-admin-status-lock::before {
            background: #d84a57;
        }
        .ui-admin-table-head,
        .ui-admin-table-row {
            display: grid;
            grid-template-columns: 1.1fr 1.25fr .85fr 1fr 1.15fr;
            align-items: center;
            gap: .7rem;
            min-height: 42px;
            padding: .45rem .7rem;
        }
        .ui-admin-table-head {
            border: 1px solid #d0def0;
            border-radius: 8px 8px 0 0;
            background: #e4efff;
            color: #21456f;
            font-size: .78rem;
            font-weight: 700;
        }
        .ui-admin-table-row {
            border-left: 1px solid #dce5ef;
            border-right: 1px solid #dce5ef;
            border-bottom: 1px solid #dce5ef;
            color: #193958;
            font-size: .82rem;
        }
        .ui-admin-table-row:nth-child(even) {
            background: rgba(235, 243, 253, .48);
        }
        .ui-admin-quick-card {
            padding: .7rem .85rem;
            margin: .2rem 0 .65rem;
            border: 1px solid #d5e2f1;
            border-radius: 10px;
            background: rgba(255,255,255,.94);
            color: #24466f;
        }
        body:has(.st-key-sales-workspace) [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #eaf3ff 0, #f7f9fd 58%, #eef4fc 100%);
        }
        body:has(.st-key-sales-workspace) .block-container {
            max-width: 1500px;
            padding-top: .55rem;
            padding-bottom: 1.5rem;
        }
        body:has(.st-key-sales-workspace) [data-testid="stHeader"] {
            background: rgba(229, 240, 255, .92);
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] {
            position: relative;
            overflow: hidden;
            border-right: 1px solid #0b5b95;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] > div:first-child {
            position: relative;
            z-index: 1;
            background: transparent;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] .ui-sidebar-brand {
            padding: .65rem .5rem;
            border: 0;
            background: transparent;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] .ui-sidebar-title,
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] .ui-sidebar-subtitle,
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] .ui-sidebar-user,
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #e8f4ff;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [data-testid="stExpander"] {
            border: 0;
            background: transparent;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [data-testid="stExpander"] summary {
            border-radius: 9px;
            color: #fff;
            background: rgba(37, 116, 177, .34);
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [role="radiogroup"] label {
            color: #eaf5ff;
            border-radius: 8px;
            padding: .45rem .55rem;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: linear-gradient(100deg, #0879ce, #138ee0);
            box-shadow: 0 4px 12px rgba(0, 32, 71, .22);
            color: #fff;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
            color: #fff;
            border-color: rgba(171, 215, 247, .45);
            background: rgba(8, 77, 132, .58);
        }
        body:has(.st-key-sales-workspace) .st-key-sales-topbar {
            padding: .05rem .3rem .4rem;
            border-bottom: 1px solid #d6e2f1;
            margin-bottom: .35rem;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-menu button,
        body:has(.st-key-sales-workspace) .st-key-sales-notifications button,
        body:has(.st-key-sales-workspace) .st-key-sales-settings button,
        body:has(.st-key-sales-workspace) .st-key-sales-profile button {
            min-height: 2.45rem;
            border: 0;
            background: transparent;
            color: #233d60;
            font-size: 1.15rem;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-menu button {
            font-size: 1.4rem;
        }
        body:has(.st-key-sales-workspace) .st-key-dashboard-profile button {
            width: 2.45rem;
            min-width: 2.45rem;
            height: 2.45rem;
            border: 0;
            border-radius: 50%;
            background: #075d9d;
            color: #fff;
            font-weight: 750;
        }
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] .st-key-sales-sidebar-profile button {
            justify-content: flex-start;
            min-height: 3.2rem;
            border: 1px solid rgba(151, 207, 246, .32);
            border-radius: 12px;
            background: rgba(5, 65, 116, .55);
            color: #f0f7ff;
            font-weight: 650;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-notifications button:hover,
        body:has(.st-key-sales-workspace) .st-key-sales-settings button:hover,
        body:has(.st-key-sales-workspace) .st-key-sales-profile button:hover,
        body:has(.st-key-sales-workspace) .st-key-sales-menu button:hover {
            color: #0876c4;
            background: #deedff;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-search [data-testid="stTextInput"] input {
            min-height: 2.45rem;
            padding-left: 2.15rem;
            border-color: #c9d8ec;
            border-radius: 9px;
            background: rgba(255,255,255,.9);
            font-size: .8rem;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-search [data-testid="stTextInput"] {
            position: relative;
        }
        body:has(.st-key-sales-workspace) .st-key-sales-search [data-testid="stTextInput"]::before {
            content: "⌕";
            position: absolute;
            z-index: 2;
            left: .65rem;
            top: .25rem;
            color: #526782;
            font-size: 1.35rem;
        }
        .ui-sales-hero {
            min-height: 124px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            margin: .1rem 0 1rem;
            padding: 1.1rem 1.7rem;
            border: 1px solid rgba(145, 197, 242, .55);
            border-radius: 15px;
            background-color: #0876c4;
            background-position: center 50%;
            background-size: cover;
            box-shadow: 0 7px 20px rgba(12, 72, 131, .13);
            color: #fff;
        }
        .ui-sales-hero .ui-eyebrow {
            color: #d7edff;
            font-size: .72rem;
            font-weight: 750;
            letter-spacing: .14em;
            text-transform: uppercase;
        }
        .ui-sales-hero h1 {
            margin: .2rem 0 0;
            color: #fff;
            font-size: clamp(1.7rem, 3vw, 2.45rem);
            font-weight: 780;
            letter-spacing: -.025em;
        }
        .ui-sales-hero p {
            margin: .35rem 0 0;
            color: #e5f3ff;
            font-size: .95rem;
        }
        body:has(.st-key-sales-workspace) [class*="st-key-sales-"] [data-testid="stVerticalBlockBorderWrapper"],
        body:has(.st-key-sales-workspace) [class*="st-key-history-"] [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: #d3e2f4;
            border-radius: 13px;
            background: rgba(255,255,255,.9);
            box-shadow: 0 3px 12px rgba(44, 75, 119, .05);
        }
        .ui-sales-card-title {
            display: flex;
            align-items: center;
            gap: .55rem;
            color: #17365f;
            font-size: 1.06rem;
            font-weight: 750;
        }
        .ui-sales-card-title svg {
            width: 23px;
            height: 23px;
            color: #0876c4;
        }
        .ui-sales-status {
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            padding: .16rem .55rem;
            border-radius: 999px;
            font-size: .76rem;
            white-space: nowrap;
        }
        .ui-sales-status-review {
            background: #fff1c9;
            color: #604a12;
        }
        .ui-sales-status-ready {
            background: #dff4e8;
            color: #176444;
        }
        .ui-sales-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #e5a500;
        }
        .ui-sales-status-ready .ui-sales-status-dot {
            background: #20a36c;
        }
        .ui-sales-history-header {
            padding: .55rem .35rem;
            border: 1px solid #d0def0;
            border-radius: 8px 8px 0 0;
            background: #e4efff;
            color: #21456f;
            font-size: .78rem;
            font-weight: 700;
        }
        .ui-sales-history-row {
            min-height: 2.7rem;
            padding: .28rem .35rem;
            border-bottom: 1px solid #e0e8f2;
            color: #193958;
            font-size: .8rem;
        }
        .ui-sales-history-row:nth-child(even) {
            background: rgba(235, 243, 253, .48);
        }
        .ui-sales-history-actions button {
            min-height: 2rem;
            padding: .2rem .35rem;
            font-size: .78rem;
        }
        .ui-sales-profile-mark {
            display: grid;
            place-items: center;
            width: 62px;
            height: 62px;
            border: 4px solid #dcecff;
            border-radius: 50%;
            background: #1684cf;
            color: #fff;
            font-size: 1.45rem;
            font-weight: 750;
        }
        .ui-sales-info-row {
            display: grid;
            grid-template-columns: minmax(110px, .7fr) 1fr;
            gap: .7rem;
            padding: .55rem .25rem;
            border-bottom: 1px solid #dce5ef;
            color: #18395b;
        }
        .ui-sales-quick-card {
            padding: .7rem .85rem;
            margin: .2rem 0 .65rem;
            border: 1px solid #d5e2f1;
            border-radius: 10px;
            background: rgba(255,255,255,.94);
            color: #24466f;
        }
        @media (max-width: 760px) {
            body:has(.st-key-sales-workspace) .block-container {
                padding-left: .8rem;
                padding-right: .8rem;
            }
            .ui-sales-hero {
                min-height: 105px;
                padding: .9rem 1rem;
                background-position: 63% center;
            }
        }
        .ui-login {
            max-width: 700px;
            margin: 8vh auto;
            padding: 1.5rem;
        }
        @media (max-height: 600px) {
            [class*="st-key-login-card"] {
                margin: 0 auto .75rem;
                padding: 1rem 1.25rem;
            }
            .ui-login-heading {
                margin-bottom: .65rem;
            }
            .ui-login-mark {
                width: 44px;
                height: 44px;
                margin-bottom: .4rem;
            }
            .ui-login-heading h1 {
                font-size: 1.4rem;
            }
            .st-key-login-card [data-testid="stForm"] {
                padding: 8px;
            }
            .st-key-login-card [data-testid="stForm"] [data-testid="stVerticalBlock"] {
                gap: .55rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def secure_transport():
    request_url = st.context.url
    if not request_url:
        return True
    parsed = urlsplit(request_url)
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    forwarded_proto = st.context.headers.get("X-Forwarded-Proto", "")
    forwarded_proto = forwarded_proto.split(",", 1)[0].strip().lower()
    return parsed.scheme == "https" or forwarded_proto == "https"


def load_fonts():
    candidates = [
        (
            Path(__file__).parent / "fonts" / "DejaVuSans.ttf",
            Path(__file__).parent / "fonts" / "DejaVuSans-Bold.ttf",
        ),
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("UI-Regular", str(regular)))
            pdfmetrics.registerFont(TTFont("UI-Bold", str(bold)))
            return "UI-Regular", "UI-Bold"
    return "Helvetica", "Helvetica-Bold"


FONT, BOLD = load_fonts()


def paragraph(value, style):
    from xml.sax.saxutils import escape

    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)


def create_pdf(rows, fields, totals):
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="UIBody",
            fontName=FONT,
            fontSize=8.2,
            leading=12,
            textColor=colors.HexColor("#273746"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="UIBold",
            parent=styles["UIBody"],
            fontName=BOLD,
        )
    )
    styles.add(
        ParagraphStyle(
            name="UIHeader",
            parent=styles["UIBody"],
            fontName=BOLD,
            textColor=colors.white,
            fontSize=7.1,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="UITitle",
            fontName=BOLD,
            fontSize=16,
            leading=22,
            alignment=TA_CENTER,
            textColor=NAVY,
        )
    )
    styles.add(
        ParagraphStyle(
            name="UISmall",
            parent=styles["UIBody"],
            fontSize=7,
            leading=10,
        )
    )

    def p(value, style="UIBody"):
        return paragraph(value, styles[style])

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=(210 * mm, 297 * mm),
        leftMargin=13 * mm,
        rightMargin=13 * mm,
        topMargin=13 * mm,
        bottomMargin=14 * mm,
        title="Ocean Export Quotation",
        author="U&I Logistics",
    )
    story = [
        PDFImage(
            str(LOGO_PATH),
            width=30 * mm,
            height=30 * mm,
            kind="proportional",
            mask="auto",
        ),
        Spacer(1, 2 * mm),
        p("U&I LOGISTICS CORPORATION", "UITitle"),
        Spacer(1, 2 * mm),
        p("OCEAN EXPORT QUOTATION", "UITitle"),
        Spacer(1, 6 * mm),
    ]
    info = [
        ("To", fields["quote_customer"]),
        ("Dated", fields["quote_date"].strftime("%d-%b-%Y")),
        ("Quotation No.", fields["quote_number"]),
        ("POL", fields["quote_pol"]),
        ("POD", fields["quote_pod"]),
        ("Commodity", fields["quote_commodity"]),
        ("Valid to", fields["quote_valid_until"].strftime("%d-%b-%Y")),
        (
            "Mode",
            f'{fields["quote_incoterm"]} | Sea FCL/FCL | '
            f'{fields["quote_qty"]} × {fields["quote_container"]}',
        ),
        ("Carrier", fields["quote_carrier"] or "To be confirmed"),
        ("Service", fields["quote_service_scope"]),
    ]
    info_table = Table(
        [[p(f"{label}:", "UIBold"), p(value)] for label, value in info],
        colWidths=[34 * mm, 150 * mm],
        hAlign="LEFT",
    )
    info_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.extend([info_table, Spacer(1, 6 * mm)])

    headings = [
        "JOB",
        "DESCRIPTION",
        "PRICE (USD)",
        "UNIT",
        "QTY",
        "AMOUNT (USD)",
        "CARRIER / NOTE",
    ]
    table_rows = [[p(heading, "UIHeader") for heading in headings]]
    for group in PUBLIC_GROUPS[:-1]:
        subset = rows[rows["Nhóm phí"].eq(group)]
        for index, (_, row) in enumerate(subset.iterrows()):
            table_rows.append(
                [
                    p(group if index == 0 else "", "UIBold"),
                    p(row["Khoản phí"]),
                    p(f'{row["Giá bán (USD)"]:,.2f}'),
                    p(row["Đơn vị"]),
                    p(f'{row["Số lượng"]:g}'),
                    p(f'{row["Thành tiền bán"]:,.2f}'),
                    p(
                        (
                            fields["quote_carrier"] + "; "
                            if group == "OCEAN FREIGHT"
                            and fields["quote_carrier"]
                            else ""
                        )
                        + row["Ghi chú"]
                    ),
                ]
            )
    table_rows.append(
        [
            p("TOTAL ESTIMATED CHARGES (USD)", "UIBold"),
            "",
            "",
            "",
            "",
            p(f'{totals["total_sell"]:,.2f}', "UIBold"),
            "",
        ]
    )
    fees_table = Table(
        table_rows,
        colWidths=[29 * mm, 43 * mm, 22 * mm, 15 * mm, 12 * mm, 25 * mm, 38 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    fees_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#9AAAB5")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF0C7")),
                ("SPAN", (0, -1), (4, -1)),
                ("ALIGN", (2, 1), (5, -1), "RIGHT"),
            ]
        )
    )
    story.extend([fees_table, Spacer(1, 6 * mm), p("Remarks:", "UIBold")])
    for note in fields["quote_notes"].splitlines():
        if note.strip():
            story.append(p("• " + note.strip(), "UISmall"))
    story.extend(
        [
            Spacer(1, 7 * mm),
            p("Thank you for giving U&I Logistics the opportunity to quote."),
            p("Best regards,"),
            Spacer(1, 2 * mm),
            p("U&I LOGISTICS CORPORATION", "UIBold"),
        ]
    )
    for label, key in (
        ("Sales", "quote_sales_name"),
        ("Phone", "quote_sales_phone"),
        ("Email", "quote_sales_email"),
    ):
        if fields[key].strip():
            story.append(p(f"{label}: {fields[key]}"))
    story.append(p("Web: www.unilogistics.vn"))

    def footer(canvas, doc):
        canvas.setFont(FONT, 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(197 * mm, 8 * mm, f"Page {doc.page}")

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _show_login():
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(ellipse at 18% 18%, #2586a8 0, transparent 42%),
                radial-gradient(ellipse at 84% 82%, #004d70 0, transparent 48%),
                linear-gradient(135deg, #006494 0%, #087ba0 52%, #004d70 100%);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="login-card"):
        st.markdown(
            f"""
            <div class="ui-login-heading">
              <img class="ui-login-mark" src="{LOGO_DATA_URI}" alt="U&amp;I Logistics logo">
              <h1>U&amp;I Logistics</h1>
              <p>Hệ thống báo giá hàng xuất nội bộ</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if not security_store.has_admin():
            st.error("Máy chủ chưa được khởi tạo tài khoản quản trị.")
            if app_config.database_url():
                st.markdown(
                    "Cấu hình các khóa khởi tạo Admin trong Streamlit Secrets "
                    "rồi khởi động lại ứng dụng:"
                )
                st.code(
                    "bootstrap_admin.username\n"
                    "bootstrap_admin.display_name\n"
                    "bootstrap_admin.password"
                )
            else:
                st.markdown(
                    "Chạy lệnh sau **trên máy chủ** để tạo tài khoản quản trị đầu tiên:"
                )
                st.code(
                    '.\\.venv\\Scripts\\python.exe manage_users.py create-admin admin "U&I Admin"',
                    language="powershell",
                )
            st.caption(
                "Mật khẩu được nhập kín trong Terminal. Không đặt mật khẩu mặc định "
                "hoặc chia sẻ tệp cơ sở dữ liệu cho Sales."
            )
            return

        with st.form("login_form"):
            st.text_input(
                "Tên đăng nhập",
                placeholder="Nhập tên đăng nhập",
                key="login_username",
            )
            st.text_input(
                "Mật khẩu",
                type="password",
                placeholder="Nhập mật khẩu",
                key="login_password",
            )
            submitted = st.form_submit_button(
                "Đăng nhập", type="primary", width="stretch"
            )
        if submitted:
            user = security_store.authenticate(
                st.session_state["login_username"],
                st.session_state["login_password"],
            )
            if user is None:
                st.error(
                    "Tên đăng nhập hoặc mật khẩu không đúng, "
                    "hoặc tài khoản bị khóa."
                )
            else:
                st.session_state["auth_user_id"] = user["id"]
                st.rerun()


def _initialize_quote_state():
    for key, value in _new_quote_defaults().items():
        if key not in st.session_state:
            st.session_state[key] = value
    if "current_quote_id" not in st.session_state:
        st.session_state["current_quote_id"] = None
    if "editor_version" not in st.session_state:
        st.session_state["editor_version"] = 0
    if "fees" not in st.session_state:
        st.session_state["fees"] = pd.DataFrame(columns=FEE_COLUMNS)
    if "edited_fees" not in st.session_state:
        st.session_state["edited_fees"] = None
    if "profit_markup_percent" not in st.session_state:
        st.session_state["profit_markup_percent"] = 0.0
    if "profit_last_applied_percent" not in st.session_state:
        st.session_state["profit_last_applied_percent"] = 0.0
    if "ai_source_name" not in st.session_state:
        st.session_state["ai_source_name"] = ""
    if "ai_warnings" not in st.session_state:
        st.session_state["ai_warnings"] = []
    if "ai_metadata" not in st.session_state:
        st.session_state["ai_metadata"] = {}
    if "saved_quote_fingerprint" not in st.session_state:
        st.session_state["saved_quote_fingerprint"] = None
    if "confirmed_pdf_fingerprint" not in st.session_state:
        st.session_state["confirmed_pdf_fingerprint"] = None


def _quote_fields():
    fields = {key: st.session_state[key] for key in QUOTE_FIELDS}
    for key in ("quote_date", "quote_valid_until"):
        value = fields[key]
        if value and isinstance(value, str):
            fields[key] = date.fromisoformat(value)
        elif not value:
            fields[key] = None
    return fields


def _quote_payload(fees):
    safe_fees = json.loads(
        fees[FEE_COLUMNS].to_json(orient="records", force_ascii=False)
    )
    fields = _quote_fields()
    fields["quote_date"] = fields["quote_date"].isoformat()
    if fields["quote_valid_until"] is not None:
        fields["quote_valid_until"] = fields["quote_valid_until"].isoformat()
    return {"fields": fields, "fees": safe_fees}


def _existing_profit_percent(fee_rows):
    calculated, _, _ = calculate_quote(fee_rows)
    eligible = (
        ~internal_fee_mask(calculated)
        & calculated["Khoản phí"].str.strip().ne("")
    )
    profit = pd.to_numeric(calculated["Profit (USD)"], errors="coerce").fillna(0)
    quantity = pd.to_numeric(calculated["Số lượng"], errors="coerce").fillna(0)
    unit_cost = pd.to_numeric(
        calculated["Giá mua (USD)"], errors="coerce"
    ).fillna(0)
    total_profit = float((profit * quantity)[eligible].sum())
    total_cost = float((unit_cost * quantity)[eligible].sum())
    if total_cost <= 0:
        return 0.0
    return max(0.0, total_profit / total_cost * 100)


def _set_profit_allocation_state(fee_rows):
    markup_percent = min(100.0, _existing_profit_percent(fee_rows))
    st.session_state["profit_markup_percent"] = markup_percent
    st.session_state["profit_last_applied_percent"] = markup_percent
    st.session_state.pop("profit_allocation_error", None)
    st.session_state.pop("profit_allocation_message", None)


def _payload_fingerprint(payload):
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _create_ai_quote_draft(
    user, result, candidate, source_name, extraction_warnings
):
    direction = str(result.get("direction") or "").lower()
    direction_tokens = set(re.findall(r"[a-z]+", direction))
    if "import" in direction_tokens and "export" not in direction_tokens:
        raise ValueError(
            "AI nhận diện đây là bảng giá hàng nhập; không tạo báo giá xuất."
        )

    fields = _new_quote_defaults()
    metadata_fields, metadata_warnings = normalize_quote_metadata(result)
    fields.update(metadata_fields)
    fields["quote_sales_name"] = user["display_name"]

    draft_fees = candidate.copy()
    draft_fees["Profit (USD)"] = None
    conditional_fees = draft_fees["Ghi chú"].fillna("").astype(str).str.startswith(
        "CONDITIONAL -"
    )
    draft_fees["Hiển thị PDF"] = (
        draft_fees["Nhóm phí"].isin(PDF_GROUPS) & ~conditional_fees
    )
    calculated, _, _ = calculate_quote(draft_fees)

    for key, value in fields.items():
        st.session_state[key] = value
    st.session_state["fees"] = calculated[FEE_COLUMNS].copy()
    st.session_state["edited_fees"] = None
    _set_profit_allocation_state(calculated)
    st.session_state["current_quote_id"] = None
    st.session_state["saved_quote_fingerprint"] = None
    st.session_state["confirmed_pdf_fingerprint"] = None
    st.session_state["editor_version"] += 1
    payload = _quote_payload(calculated)
    quote_id = security_store.save_quote(
        user["id"],
        None,
        fields["quote_number"],
        fields["quote_customer"],
        payload,
    )
    st.session_state["current_quote_id"] = quote_id
    st.session_state["saved_quote_fingerprint"] = _payload_fingerprint(payload)
    st.session_state["ai_source_name"] = source_name
    st.session_state["ai_metadata"] = {
        key: result.get(key, "")
        for key in (
            "direction",
            "customer",
            "pol",
            "pod",
            "incoterm",
            "valid_until",
            "commodity",
            "carrier",
            "container_type",
            "container_quantity",
            "currency",
        )
    }
    st.session_state["ai_warnings"] = extraction_warnings + metadata_warnings
    return fields["quote_number"]


def _load_saved_quote(user_id, quote_id):
    saved = security_store.get_quote(user_id, quote_id)
    if saved is None:
        st.error("Không tìm thấy báo giá hoặc bạn không có quyền truy cập.")
        return
    payload = saved["payload"]
    for key, value in payload["fields"].items():
        if key == "quote_date" or (
            key == "quote_valid_until" and value is not None
        ):
            value = date.fromisoformat(value)
        st.session_state[key] = value
    st.session_state["fees"] = pd.DataFrame(payload["fees"])
    st.session_state["edited_fees"] = None
    _set_profit_allocation_state(st.session_state["fees"])
    st.session_state["current_quote_id"] = saved["id"]
    st.session_state["saved_quote_fingerprint"] = _payload_fingerprint(payload)
    st.session_state["confirmed_pdf_fingerprint"] = None
    st.session_state["editor_version"] += 1
    st.session_state["ai_source_name"] = ""
    st.session_state["ai_warnings"] = []
    st.session_state["ai_metadata"] = {}
    st.session_state["open_quote_workspace"] = True
    st.rerun()


def _start_new_quote():
    for key, value in _new_quote_defaults().items():
        st.session_state[key] = value
    st.session_state["current_quote_id"] = None
    st.session_state["fees"] = pd.DataFrame(columns=FEE_COLUMNS)
    st.session_state["edited_fees"] = None
    _set_profit_allocation_state(st.session_state["fees"])
    st.session_state["saved_quote_fingerprint"] = None
    st.session_state["confirmed_pdf_fingerprint"] = None
    st.session_state["editor_version"] += 1
    st.session_state["ai_source_name"] = ""
    st.session_state["ai_warnings"] = []
    st.session_state["ai_metadata"] = {}
    st.rerun()


def _sidebar(user, show_navigation=True):
    st.sidebar.markdown(
        f"""
        <div class="ui-sidebar-brand">
          <img class="ui-sidebar-mark" src="{LOGO_DATA_URI}" alt="U&amp;I Logistics logo">
          <div>
            <div class="ui-sidebar-title">U&amp;I Logistics</div>
            <div class="ui-sidebar-subtitle">{
                "SALES WORKSPACE" if user["role"] == "sales" else "ADMINISTRATION"
            }</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if user["role"] == "sales":
        st.sidebar.button(
            f'👤 {user["display_name"]}  ▾',
            key="sales-sidebar-profile",
            help="Mở menu tài khoản",
            width="stretch",
            on_click=_toggle_sales_profile,
        )
        st.sidebar.caption("Sales")
    else:
        st.sidebar.markdown(
            f'<div class="ui-sidebar-user"><strong>{escape(user["display_name"])}</strong><br>'
            "Quản trị viên</div>",
            unsafe_allow_html=True,
        )

    active_page = None
    if show_navigation and user["role"] == "admin":
        with st.sidebar.expander("QUẢN TRỊ", expanded=True):
            active_page = st.radio(
                "Khu vực",
                options=["accounts", "settings"],
                format_func=lambda page: (
                    "👥 Quản lý tài khoản"
                    if page == "accounts"
                    else "⚙️ Cài đặt hệ thống"
                ),
                key="admin_navigation",
                label_visibility="collapsed",
            )
    elif show_navigation:
        with st.sidebar.expander("KINH DOANH", expanded=True):
            active_page = st.radio(
                "Khu vực",
                options=["dashboard", "quotes", "history", "account"],
                format_func=lambda page: {
                    "dashboard": "▦ Tổng quan",
                    "quotes": "＋ Tạo báo giá",
                    "history": "▣ Báo giá của tôi",
                    "account": "♙ Tài khoản",
                }[page],
                key="sales_navigation",
                label_visibility="collapsed",
            )
    st.sidebar.markdown("---")
    if st.sidebar.button("↪  Đăng xuất", width="stretch", key="sidebar_logout"):
        st.session_state.clear()
        st.rerun()
    return active_page


def _search_sales_quotes():
    if str(st.session_state.get("dashboard-search", "")).strip():
        st.session_state["sales_navigation"] = "history"
        st.session_state["history_page_number"] = 1
        st.session_state["dashboard_show_notifications"] = False
        st.session_state["dashboard_show_profile"] = False


def _sales_topbar(user):
    st.markdown(
        f"""
        <style>
        body:has(.st-key-sales-workspace) [data-testid="stSidebar"] {{
            background:
                linear-gradient(180deg, rgba(0, 49, 91, .94), rgba(0, 75, 126, .92)),
                url("{DASHBOARD_SIDEBAR_DATA_URI}") center bottom / 100% auto no-repeat,
                #063d70;
        }}
        </style>
        <div class="st-key-sales-workspace"></div>
        """,
        unsafe_allow_html=True,
    )
    if st.session_state.get("dashboard_sidebar_collapsed", False):
        st.markdown(
            """
            <style>
            body:has(.st-key-sales-workspace) [data-testid="stSidebar"] {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    avatar_letter = escape(str(user["display_name"]).strip()[:1].upper() or "S")
    with st.container(key="dashboard-topbar"):
        toolbar = st.columns([.55, 5, .65, .65, .75], vertical_alignment="center")
        with toolbar[0]:
            st.button(
                "☰",
                key="dashboard-menu",
                help="Thu gọn hoặc mở thanh điều hướng",
                on_click=_toggle_dashboard_sidebar,
            )
        with toolbar[1]:
            st.text_input(
                "Tìm kiếm",
                placeholder="Tìm mã báo giá hoặc khách hàng...",
                label_visibility="collapsed",
                key="dashboard-search",
                on_change=_search_sales_quotes,
            )
        with toolbar[2]:
            st.button(
                "🔔",
                key="dashboard-notifications",
                help="Thông báo báo giá",
                on_click=_toggle_dashboard_notifications,
            )
        with toolbar[3]:
            st.button(
                "⚙",
                key="dashboard-settings",
                help="Mở trang tài khoản",
                on_click=_open_sales_account,
            )
        with toolbar[4]:
            st.button(
                avatar_letter,
                key="dashboard-profile",
                help="Thông tin tài khoản",
                on_click=_toggle_dashboard_profile,
            )

    if st.session_state.get("dashboard_show_notifications", False):
        needs_review = 0
        if st.session_state.get("sales_notifications_enabled", True):
            quotes = security_store.list_quotes(user["id"])
            for quote in quotes:
                detail = security_store.get_quote(user["id"], quote["id"])
                if detail is not None and _quote_pdf_errors(detail):
                    needs_review += 1
        with st.container(key="dashboard-notification-panel"):
            if not st.session_state.get("sales_notifications_enabled", True):
                message = "Thông báo đang tắt trong phần Tài khoản."
            elif needs_review:
                message = (
                    f"{needs_review} báo giá cần hoàn thiện dữ liệu trước khi xuất PDF."
                )
            else:
                message = "Hiện không có báo giá nào đang chờ hoàn thiện."
            st.markdown(
                '<div class="ui-sales-quick-card"><strong>Thông báo</strong><br>'
                f"{message}</div>",
                unsafe_allow_html=True,
            )
            st.button(
                "Xem báo giá của tôi",
                key="dashboard-notifications-history",
                on_click=_open_sales_history,
            )

    if st.session_state.get("dashboard_show_profile", False):
        with st.container(key="dashboard-profile-panel"):
            st.markdown(
                '<div class="ui-sales-quick-card"><strong>'
                f'{escape(str(user["display_name"]))}</strong><br>'
                "Nhân viên kinh doanh</div>",
                unsafe_allow_html=True,
            )
            profile_actions = st.columns(2)
            profile_actions[0].button(
                "Tài khoản",
                key="dashboard-profile-account",
                on_click=_open_sales_account,
            )
            profile_actions[1].button(
                "Đăng xuất",
                key="dashboard-profile-logout",
                on_click=_logout_from_dashboard,
            )


def _sales_hero(eyebrow, title, description):
    st.markdown(
        f'<div class="ui-sales-hero" style="background-image:'
        f'linear-gradient(90deg, rgba(0, 68, 127, .98) 0%, '
        f'rgba(0, 105, 176, .84) 38%, rgba(0, 113, 192, .12) 100%), '
        f'url(&quot;{DASHBOARD_ART_DATA_URI}&quot;)">'
        f'<div class="ui-eyebrow">{escape(eyebrow)}</div>'
        f"<h1>{escape(title)}</h1><p>{escape(description)}</p></div>",
        unsafe_allow_html=True,
    )


def _open_admin_settings():
    st.session_state["admin_navigation"] = "settings"
    st.session_state["admin_show_profile"] = False
    st.session_state["admin_show_notifications"] = False


def _open_admin_accounts():
    st.session_state["admin_navigation"] = "accounts"
    st.session_state["admin_show_profile"] = False
    st.session_state["admin_show_notifications"] = False


def _toggle_admin_sidebar():
    st.session_state["admin_sidebar_collapsed"] = not st.session_state.get(
        "admin_sidebar_collapsed", False
    )


def _toggle_admin_notifications():
    st.session_state["admin_show_notifications"] = not st.session_state.get(
        "admin_show_notifications", False
    )
    st.session_state["admin_show_profile"] = False


def _toggle_admin_profile():
    st.session_state["admin_show_profile"] = not st.session_state.get(
        "admin_show_profile", False
    )
    st.session_state["admin_show_notifications"] = False


def _logout_from_admin():
    st.session_state.clear()


def _admin_hero(eyebrow, title, description):
    st.markdown(
        f'<div class="ui-admin-hero" style="background-image:'
        f'linear-gradient(90deg, rgba(0, 68, 127, .98) 0%, '
        f'rgba(0, 105, 176, .84) 38%, rgba(0, 113, 192, .12) 100%), '
        f'url(&quot;{DASHBOARD_ART_DATA_URI}&quot;)">'
        f'<div class="ui-eyebrow">{escape(eyebrow)}</div>'
        f"<h1>{escape(title)}</h1><p>{escape(description)}</p></div>",
        unsafe_allow_html=True,
    )


def _admin_topbar(user):
    st.markdown(
        f"""
        <style>
        body:has(.st-key-admin-workspace) [data-testid="stSidebar"] {{
            background:
                linear-gradient(180deg, rgba(0, 49, 91, .96), rgba(0, 75, 126, .94)),
                url("{DASHBOARD_SIDEBAR_DATA_URI}") center bottom / 100% auto no-repeat,
                #063d70;
        }}
        </style>
        <div class="st-key-admin-workspace"></div>
        """,
        unsafe_allow_html=True,
    )
    if st.session_state.get("admin_sidebar_collapsed", False):
        st.markdown(
            """
            <style>
            body:has(.st-key-admin-workspace) [data-testid="stSidebar"] {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    avatar_letter = escape(str(user["display_name"]).strip()[:1].upper() or "A")
    with st.container(key="admin-topbar"):
        toolbar = st.columns([.55, 5, .65, .65, .75], vertical_alignment="center")
        with toolbar[0]:
            st.button(
                "☰",
                key="admin-menu",
                help="Thu gọn hoặc mở thanh điều hướng",
                on_click=_toggle_admin_sidebar,
            )
        with toolbar[1]:
            st.text_input(
                "Tìm kiếm quản trị",
                placeholder="Tìm tên đăng nhập hoặc nhân viên...",
                label_visibility="collapsed",
                key="admin-account-search",
            )
        with toolbar[2]:
            st.button(
                "🔔",
                key="admin-notifications",
                help="Thông báo quản trị",
                on_click=_toggle_admin_notifications,
            )
        with toolbar[3]:
            st.button(
                "⚙",
                key="admin-settings",
                help="Mở cài đặt hệ thống",
                on_click=_open_admin_settings,
            )
        with toolbar[4]:
            st.button(
                avatar_letter,
                key="admin-profile",
                help="Thông tin quản trị viên",
                on_click=_toggle_admin_profile,
            )

    if st.session_state.get("admin_show_notifications", False):
        sales_users = [
            account for account in security_store.list_users()
            if account["role"] == "sales"
        ]
        locked_count = sum(not account["active"] for account in sales_users)
        st.markdown(
            '<div class="ui-admin-quick-card"><strong>Thông báo quản trị</strong><br>'
            f'{len(sales_users)} tài khoản Sales · {locked_count} tài khoản đang khóa.</div>',
            unsafe_allow_html=True,
        )
        st.button(
            "Quản lý tài khoản",
            key="admin-notifications-account",
            on_click=_open_admin_accounts,
        )

    if st.session_state.get("admin_show_profile", False):
        st.markdown(
            '<div class="ui-admin-quick-card"><strong>'
            f'{escape(str(user["display_name"]))}</strong><br>Quản trị viên hệ thống</div>',
            unsafe_allow_html=True,
        )
        profile_actions = st.columns(2)
        profile_actions[0].button(
            "Cài đặt",
            key="admin-profile-settings",
            on_click=_open_admin_settings,
        )
        profile_actions[1].button(
            "Đăng xuất",
            key="admin-profile-logout",
            on_click=_logout_from_admin,
        )


def _change_password_form(user):
    st.subheader("Đổi mật khẩu")
    with st.form("change_password_form"):
        new_password = st.text_input(
            "Mật khẩu mới", type="password", key="new_password"
        )
        confirmation = st.text_input(
            "Nhập lại mật khẩu mới",
            type="password",
            key="new_password_confirmation",
        )
        submitted = st.form_submit_button("CẬP NHẬT MẬT KHẨU", type="primary")
    if submitted:
        if new_password != confirmation:
            st.error("Hai mật khẩu không khớp.")
        else:
            try:
                security_store.change_password(user["id"], new_password)
            except (ValueError, *security_store.DATABASE_ERRORS) as error:
                st.error(str(error))
            else:
                st.session_state["clear_password_inputs"] = True
                st.success("Mật khẩu đã được cập nhật.")
                st.rerun()


def _admin_page(user):
    _admin_hero(
        "Administration",
        "Quản lý tài khoản",
        "Tạo và quản lý tài khoản Sales; quản trị viên không xem được báo giá của Sales.",
    )
    create_column, list_column = st.columns([.78, 1.22])
    with create_column:
        with st.container(key="admin-create-card", border=True):
            st.markdown(
                '<div class="ui-admin-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21a8 8 0 0 1 16 0" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
                '<path d="M19 8v6m-3-3h6" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round"/></svg>Tạo tài khoản Sales</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")
            with st.form("create_sales_form", clear_on_submit=True):
                username = st.text_input("Tên đăng nhập")
                display_name = st.text_input("Tên nhân viên")
                password = st.text_input(
                    "Mật khẩu ban đầu",
                    type="password",
                    key="admin_create_password",
                )
                confirmation = st.text_input(
                    "Nhập lại mật khẩu",
                    type="password",
                    key="admin_create_password_confirmation",
                )
                submitted = st.form_submit_button("TẠO TÀI KHOẢN", type="primary")
    if submitted:
        if password != confirmation:
            st.error("Hai mật khẩu không khớp.")
        else:
            try:
                security_store.create_user(
                    username,
                    display_name,
                    password,
                    role="sales",
                    must_change_password=True,
                )
            except (
                ValueError,
                *security_store.DATABASE_ERRORS,
                OSError,
            ) as error:
                st.error(f"Không thể tạo tài khoản: {error}")
            else:
                st.session_state["clear_password_inputs"] = True
                st.success(
                    "Tài khoản đã tạo. Sales phải đổi mật khẩu khi đăng nhập lần đầu."
                )
                st.rerun()

    sales_users = [
        account for account in security_store.list_users()
        if account["role"] == "sales"
    ]
    query = str(st.session_state.get("admin-account-search", "")).strip().casefold()
    if query:
        sales_users = [
            account for account in sales_users
            if query in account["username"].casefold()
            or query in account["display_name"].casefold()
        ]
    with list_column:
        with st.container(key="admin-list-card", border=True):
            st.markdown(
                '<div class="ui-admin-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 '
                '0 1 0 0-8 4 4 0 0 0 0 8Zm13 10v-2a4 4 0 0 0-3-3.87M16 3.13'
                'a4 4 0 0 1 0 7.75" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                'Tài khoản đã cấp</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")
            if sales_users:
                st.markdown(
                    '<div class="ui-admin-table-head">'
                    "<span>Tên đăng nhập</span><span>Nhân viên</span>"
                    "<span>Trạng thái</span><span>Mật khẩu đầu</span>"
                    "<span>Tạo lúc</span></div>",
                    unsafe_allow_html=True,
                )
                for account in sales_users:
                    status_class = (
                        "ui-admin-status"
                        if account["active"]
                        else "ui-admin-status ui-admin-status-lock"
                    )
                    status_text = "Đang hoạt động" if account["active"] else "Đã khóa"
                    password_text = (
                        "Bắt buộc" if account["must_change_password"] else "Không"
                    )
                    st.markdown(
                        '<div class="ui-admin-table-row">'
                        f'<strong>{escape(account["username"])}</strong>'
                        f'<span>{escape(account["display_name"])}</span>'
                        f'<span class="{status_class}">{status_text}</span>'
                        f'<span>{password_text}</span>'
                        f'<span>{escape(account["created_at"])}</span></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info(
                    "Không tìm thấy tài khoản phù hợp."
                    if query
                    else "Chưa có tài khoản Sales."
                )

    all_sales_users = [
        account for account in security_store.list_users()
        if account["role"] == "sales"
    ]
    if all_sales_users:
        selected_id = st.selectbox(
            "Chọn tài khoản Sales",
            options=[account["id"] for account in all_sales_users],
            format_func=lambda account_id: next(
                f'{account["display_name"]} ({account["username"]})'
                for account in all_sales_users
                if account["id"] == account_id
            ),
            key="admin_selected_user",
        )
        selected_user = next(
            account for account in all_sales_users if account["id"] == selected_id
        )
        left, right = st.columns(2)
        with left:
            label = "Khóa tài khoản" if selected_user["active"] else "Mở khóa tài khoản"
            if st.button(label, key="toggle_sales_account"):
                try:
                    security_store.set_user_active(
                        selected_user["id"], not selected_user["active"]
                    )
                except (
                    ValueError,
                    *security_store.DATABASE_ERRORS,
                ) as error:
                    st.error(f"Không thể cập nhật tài khoản: {error}")
                else:
                    st.success("Đã cập nhật trạng thái tài khoản.")
                    st.rerun()
        with right:
            with st.form("reset_sales_password"):
                reset_password = st.text_input(
                    "Mật khẩu mới cấp lại",
                    type="password",
                    key="admin_reset_password",
                )
                reset_confirmation = st.text_input(
                    "Nhập lại mật khẩu",
                    type="password",
                    key="admin_reset_password_confirmation",
                )
                reset_submitted = st.form_submit_button("CẤP LẠI MẬT KHẨU")
            if reset_submitted:
                if reset_password != reset_confirmation:
                    st.error("Hai mật khẩu không khớp.")
                else:
                    try:
                        security_store.reset_user_password(
                            selected_user["id"], reset_password
                        )
                    except (
                        ValueError,
                        *security_store.DATABASE_ERRORS,
                    ) as error:
                        st.error(str(error))
                    else:
                        st.session_state["clear_password_inputs"] = True
                        st.success(
                            "Đã cấp lại mật khẩu. Sales phải đổi mật khẩu khi đăng nhập."
                        )
                        st.rerun()
    else:
        st.info("Chưa có tài khoản Sales.")
    st.markdown("---")
    _change_password_form(user)


def _sales_account_page(user):
    _sales_hero(
        "Tài khoản",
        "Thông tin tài khoản",
        "Quản lý thông tin cá nhân và quyền truy cập hệ thống.",
    )
    profile_column, settings_column = st.columns([1.05, 1])
    with profile_column:
        with st.container(key="sales-profile-card", border=True):
            st.markdown(
                '<div class="ui-sales-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21a8 8 0 0 1 16 0" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
                '</svg>Thông tin cá nhân</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")
            profile_summary = st.columns([.32, 1])
            profile_summary[0].markdown(
                f'<div class="ui-sales-profile-mark">'
                f'{escape(user["display_name"].strip()[:1].upper() or "S")}</div>',
                unsafe_allow_html=True,
            )
            profile_summary[1].markdown(
                f'<h2 style="margin:.25rem 0;color:#102b53">'
                f'{escape(user["display_name"])}</h2>'
                '<span class="ui-sales-status ui-sales-status-ready">Sales</span>',
                unsafe_allow_html=True,
            )
            for label, value in (
                ("Tên đăng nhập", user["username"]),
                ("Bộ phận", "Kinh doanh"),
                ("Vai trò", "Sales"),
                (
                    "Trạng thái",
                    "Đang hoạt động" if user["active"] else "Đã khóa",
                ),
            ):
                st.markdown(
                    '<div class="ui-sales-info-row">'
                    f'<span>{escape(label)}</span>'
                    f'<strong>{escape(str(value))}</strong></div>',
                    unsafe_allow_html=True,
                )
            if st.button(
                "✎  Chỉnh sửa thông tin",
                key="sales_profile_edit_button",
            ):
                st.session_state["sales_profile_edit_open"] = not st.session_state.get(
                    "sales_profile_edit_open", False
                )
            if st.session_state.get("sales_profile_edit_open", False):
                with st.form("sales_profile_form"):
                    display_name = st.text_input(
                        "Họ và tên",
                        value=user["display_name"],
                        max_chars=120,
                        key="sales_profile_display_name",
                    )
                    submitted = st.form_submit_button(
                        "LƯU THÔNG TIN",
                        type="primary",
                    )
                if submitted:
                    try:
                        security_store.update_display_name(
                            user["id"], display_name
                        )
                    except (
                        ValueError,
                        *security_store.DATABASE_ERRORS,
                    ) as error:
                        st.error(f"Không thể cập nhật thông tin: {error}")
                    else:
                        st.session_state["sales_profile_edit_open"] = False
                        st.success("Đã cập nhật tên hiển thị.")
                        st.rerun()

    with settings_column:
        with st.container(key="sales-settings-card", border=True):
            st.markdown(
                '<div class="ui-sales-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.64 5.64l1.42 1.42m9.88 '
                '9.88 1.42 1.42m0-12.72-1.42 1.42m-9.88 9.88-1.42 1.42" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
                '<circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2"/>'
                '</svg>Thiết lập tài khoản</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")
            if "sales_notifications_enabled" not in st.session_state:
                st.session_state["sales_notifications_enabled"] = True
            if "sales_theme" not in st.session_state:
                st.session_state["sales_theme"] = "Sáng"
            st.toggle(
                "Thông báo trong hệ thống",
                key="sales_notifications_enabled",
                help="Bật hoặc tắt thông báo báo giá trong phiên đăng nhập này.",
            )
            st.selectbox(
                "Giao diện",
                ["Sáng", "Tối"],
                key="sales_theme",
                help="Thay đổi giao diện cho phiên đăng nhập hiện tại.",
            )
            st.caption(
                "Tùy chọn thông báo và giao diện chỉ áp dụng trong phiên đăng nhập hiện tại."
            )
            st.markdown("---")
            _change_password_form(user)


def _admin_settings_page(user):
    ai_config = app_config.ai_settings()
    is_cloud_ai = ai_config["provider"] == "openai_compatible"
    _admin_hero(
        "System settings",
        "Cài đặt hệ thống",
        (
            "Cấu hình AI Cloud và quyền truy cập dành cho hệ thống."
            if is_cloud_ai
            else "Cấu hình mô hình AI và quyền truy cập dành cho máy chủ nội bộ."
        ),
    )
    settings_column, model_column = st.columns([1, 1])
    with model_column:
        with st.container(key="admin-model-card", border=True):
            heading = "AI Cloud" if is_cloud_ai else "Mô hình Ollama"
            st.markdown(
                '<div class="ui-admin-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M9 3v3m6-3v3M5 8h14v10H5zM2 12h3m14 0h3M9 21v-3m6 '
                '3v-3" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round"/></svg>'
                f"{heading}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("---")
            if is_cloud_ai:
                st.caption(
                    "Cấu hình AI Cloud được quản lý trong Streamlit Secrets. "
                    "Khóa API không hiển thị trong ứng dụng."
                )
                st.info(
                    "Nhân viên vẫn có thể nhập liệu thủ công nếu AI Cloud "
                    "không khả dụng."
                )
            else:
                st.caption(
                    "Cấu hình này áp dụng chung cho chức năng đọc Pricing; "
                    "Sales không thể xem hoặc thay đổi thông số kỹ thuật."
                )
                with st.form("ollama_settings_form"):
                    model = st.text_input(
                        "Tên mô hình Ollama",
                        value=security_store.get_ollama_model(),
                        max_chars=128,
                    )
                    submitted = st.form_submit_button(
                        "LƯU CẤU HÌNH", type="primary"
                    )
                if submitted:
                    try:
                        security_store.set_ollama_model(user["id"], model)
                    except (
                        ValueError,
                        PermissionError,
                        *security_store.DATABASE_ERRORS,
                    ) as error:
                        st.error(f"Không thể lưu cấu hình: {error}")
                    else:
                        st.success("Đã lưu mô hình AI cho toàn hệ thống.")
    with settings_column:
        with st.container(key="admin-system-card", border=True):
            st.markdown(
                '<div class="ui-admin-card-title">'
                '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2m0-14-2 '
                '2M7 17l-2 2" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round"/><circle cx="12" cy="12" r="4" '
                'stroke="currentColor" stroke-width="2"/></svg>Thiết lập hệ thống</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")
            sales_count = sum(
                account["role"] == "sales"
                for account in security_store.list_users()
            )
            active_count = sum(
                account["role"] == "sales" and account["active"]
                for account in security_store.list_users()
            )
            st.metric("Tài khoản Sales đang hoạt động", active_count)
            st.caption(f"Tổng tài khoản Sales: {sales_count}")
            st.toggle(
                "Thông báo vận hành",
                key="admin_notifications_enabled",
                value=True,
                help="Hiển thị các cảnh báo quản trị trong phiên hiện tại.",
            )
            st.caption(
                "Sales không xem mô hình AI, dữ liệu báo giá hoặc quyền quản trị."
            )


def _quote_pdf_errors(quote):
    payload = quote["payload"]
    fields = payload.get("fields", {})
    fees = pd.DataFrame(payload.get("fees", []), columns=FEE_COLUMNS)
    calculated, public_rows, _ = calculate_quote(fees)
    return validate_pdf(fields, public_rows, calculated)


def _toggle_dashboard_sidebar():
    st.session_state["dashboard_sidebar_collapsed"] = not st.session_state.get(
        "dashboard_sidebar_collapsed", False
    )


def _toggle_dashboard_notifications():
    st.session_state["dashboard_show_notifications"] = not st.session_state.get(
        "dashboard_show_notifications", False
    )
    st.session_state["dashboard_show_profile"] = False


def _toggle_dashboard_profile():
    st.session_state["dashboard_show_profile"] = not st.session_state.get(
        "dashboard_show_profile", False
    )
    st.session_state["dashboard_show_notifications"] = False


def _toggle_sales_profile():
    _toggle_dashboard_profile()


def _open_sales_account():
    st.session_state["sales_navigation"] = "account"
    st.session_state["dashboard_show_profile"] = False
    st.session_state["dashboard_show_notifications"] = False


def _open_sales_history():
    st.session_state["sales_navigation"] = "history"
    st.session_state["dashboard_show_profile"] = False
    st.session_state["dashboard_show_notifications"] = False


def _logout_from_dashboard():
    st.session_state.clear()


def _apply_sales_theme():
    if st.session_state.get("sales_theme", "Sáng") != "Tối":
        return
    st.markdown(
        """
        <style>
        body:has(.st-key-sales-workspace) [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #10243a 0, #142d47 58%, #10243a 100%);
        }
        body:has(.st-key-sales-workspace) [data-testid="stMain"] {
            color: #e7f1fc;
        }
        body:has(.st-key-sales-workspace) h1,
        body:has(.st-key-sales-workspace) h2,
        body:has(.st-key-sales-workspace) h3,
        body:has(.st-key-sales-workspace) p,
        body:has(.st-key-sales-workspace) label,
        body:has(.st-key-sales-workspace) [data-testid="stMarkdownContainer"],
        body:has(.st-key-sales-workspace) [data-testid="stCaptionContainer"] {
            color: #e1edf9;
        }
        body:has(.st-key-sales-workspace) .ui-sales-card-title,
        body:has(.st-key-sales-workspace) .ui-sales-info-row,
        body:has(.st-key-sales-workspace) .ui-sales-history-row {
            color: #e1edf9;
        }
        body:has(.st-key-sales-workspace) [data-testid="stVerticalBlockBorderWrapper"],
        body:has(.st-key-sales-workspace) [class*="st-key-dashboard-chart-card"],
        body:has(.st-key-sales-workspace) [class*="st-key-dashboard-recent-card"] {
            border-color: #31516f !important;
            background: #193550 !important;
            box-shadow: 0 4px 14px rgba(0, 0, 0, .18) !important;
        }
        body:has(.st-key-sales-workspace) .ui-sales-history-header {
            border-color: #31516f;
            background: #234766;
            color: #e8f4ff;
        }
        body:has(.st-key-sales-workspace) .ui-sales-history-row {
            border-color: #31516f;
        }
        body:has(.st-key-sales-workspace) .ui-sales-history-row:nth-child(even) {
            background: rgba(31, 65, 96, .55);
        }
        body:has(.st-key-sales-workspace) .ui-sales-info-row {
            border-color: #31516f;
        }
        body:has(.st-key-sales-workspace) [data-testid="stTextInput"] input,
        body:has(.st-key-sales-workspace) [data-testid="stNumberInput"] input,
        body:has(.st-key-sales-workspace) [data-testid="stDateInput"] input,
        body:has(.st-key-sales-workspace) [data-testid="stTextArea"] textarea,
        body:has(.st-key-sales-workspace) [data-testid="stSelectbox"] [role="combobox"] {
            border-color: #456582;
            background: #102a42;
            color: #edf6ff;
        }
        body:has(.st-key-sales-workspace) .st-key-dashboard-topbar {
            border-color: #31516f;
        }
        body:has(.st-key-sales-workspace) .st-key-dashboard-menu button,
        body:has(.st-key-sales-workspace) .st-key-dashboard-notifications button,
        body:has(.st-key-sales-workspace) .st-key-dashboard-settings button,
        body:has(.st-key-sales-workspace) .st-key-dashboard-profile button {
            color: #e6f2ff;
        }
        body:has(.st-key-sales-workspace) .ui-sales-quick-card {
            border-color: #31516f;
            background: #193550;
            color: #e1edf9;
        }
        body:has(.st-key-sales-workspace) .ui-dashboard-info {
            background: #193550;
        }
        body:has(.st-key-sales-workspace) .ui-dashboard-recent-table td {
            border-color: #31516f;
            background: #193550;
            color: #e1edf9;
        }
        body:has(.st-key-sales-workspace) .ui-dashboard-recent-table th {
            border-color: #31516f;
            background: #234766;
            color: #e8f4ff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sales_dashboard(user):
    quotes = security_store.list_quotes(user["id"])
    quote_details = [
        (quote, security_store.get_quote(user["id"], quote["id"]))
        for quote in quotes
    ]
    quote_details = [
        (summary, detail)
        for summary, detail in quote_details
        if detail is not None
    ]

    now = pd.Timestamp.now().normalize()
    month_start = now.replace(day=1)
    updated_dates = pd.to_datetime(
        [summary["updated_at"] for summary, _ in quote_details],
        errors="coerce",
    )
    active_month_updates = int((updated_dates >= month_start).sum())
    customer_names = {
        summary["customer"].strip()
        for summary, _ in quote_details
        if summary["customer"].strip()
    }
    needs_review = sum(bool(_quote_pdf_errors(detail)) for _, detail in quote_details)

    st.markdown(
        f"""
        <style>
        .ui-dashboard-title-row {{
            display: flex;
            align-items: center;
            gap: .7rem;
        }}
        .ui-dashboard-title-icon {{
            display: inline-flex;
            flex: 0 0 32px;
            align-items: center;
            justify-content: center;
            color: #0876c4;
        }}
        .ui-dashboard-title-icon svg {{
            width: 30px;
            height: 30px;
        }}
        .ui-dashboard-section-heading {{
            display: flex;
            align-items: center;
            gap: .55rem;
            color: #17365f;
            font-size: 1.08rem;
            font-weight: 750;
        }}
        .ui-dashboard-section-heading svg {{
            width: 22px;
            height: 22px;
            color: #0876c4;
        }}
        .ui-dashboard-recent-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            overflow: hidden;
            border: 1px solid #d4e0ef;
            border-radius: 9px;
            color: #193958;
            font-size: .83rem;
        }}
        .ui-dashboard-recent-table th {{
            padding: .48rem .7rem;
            border-right: 1px solid #d3deeb;
            background: #e4efff;
            color: #21456f;
            text-align: left;
            font-weight: 700;
        }}
        .ui-dashboard-recent-table td {{
            padding: .45rem .7rem;
            border-top: 1px solid #dce5ef;
            border-right: 1px solid #dce5ef;
            background: rgba(255,255,255,.84);
        }}
        .ui-dashboard-recent-table th:last-child,
        .ui-dashboard-recent-table td:last-child {{
            border-right: 0;
        }}
        .ui-dashboard-status {{
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            padding: .12rem .52rem;
            border-radius: 999px;
            background: #fff1c9;
            color: #604a12;
            font-size: .76rem;
            white-space: nowrap;
        }}
        .ui-dashboard-status::before {{
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #e5a500;
        }}
        .ui-dashboard-status-ready {{
            background: #dff4e8;
            color: #176444;
        }}
        .ui-dashboard-status-ready::before {{
            background: #20a36c;
        }}
        .ui-dashboard-no-results {{
            padding: .85rem;
            border: 1px dashed #c9d8ec;
            border-radius: 8px;
            color: #60738b;
            text-align: center;
        }}
        .ui-dashboard-hero {{
            min-height: 92px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            margin: -.1rem -1.15rem .65rem;
            padding: .85rem 1.25rem;
            border-radius: 12px;
            background:
                linear-gradient(90deg, rgba(222,239,255,.98) 0%, rgba(222,239,255,.88) 34%, rgba(222,239,255,.12) 100%),
                url("{DASHBOARD_ART_DATA_URI}") center 48% / cover no-repeat,
                #dcecff;
        }}
        .ui-dashboard-hero h1 {{
            margin: 0;
            color: #102b53;
            font-size: clamp(1.5rem, 2.2vw, 2rem);
            font-weight: 780;
            letter-spacing: -.025em;
        }}
        .ui-dashboard-hero p {{
            margin: .14rem 0 0;
            color: #243f67;
            font-size: .88rem;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"] {{
            position: relative;
            min-height: 88px;
            overflow: hidden;
            padding: .65rem .8rem;
            border: 1px solid #c9dcf4;
            border-radius: 13px;
            box-shadow: 0 3px 8px rgba(45, 80, 135, .07);
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"]::after {{
            content: "";
            position: absolute;
            right: -12px;
            bottom: -27px;
            width: 52%;
            height: 65px;
            border-radius: 50% 50% 0 0;
            background: rgba(105, 173, 244, .13);
            transform: rotate(-7deg);
            pointer-events: none;
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-0 {{
            background: linear-gradient(110deg, #fff, #edf5ff);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-1 {{
            border-color: #bce7d4;
            background: linear-gradient(110deg, #fff, #e9f8f0);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-1::after {{
            background: rgba(61, 190, 133, .13);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-2 {{
            border-color: #d7d4fb;
            background: linear-gradient(110deg, #fff, #f0efff);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-2::after {{
            background: rgba(124, 108, 237, .12);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-3 {{
            border-color: #f0dfc6;
            background: linear-gradient(110deg, #fff, #fff6e9);
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-3::after {{
            background: rgba(241, 161, 52, .13);
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"] [data-testid="stMetric"] {{
            position: relative;
            z-index: 1;
            border: 0;
            background: transparent;
            box-shadow: none;
            padding: .1rem .2rem .1rem 3.6rem;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"] [data-testid="stMetricLabel"] {{
            color: #233d60;
            font-size: .8rem;
            font-weight: 650;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"] [data-testid="stMetricValue"] {{
            color: #102b53;
            font-size: 1.55rem;
            font-weight: 780;
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-0 [data-testid="stMetricLabel"]::before,
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-1 [data-testid="stMetricLabel"]::before,
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-2 [data-testid="stMetricLabel"]::before,
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-3 [data-testid="stMetricLabel"]::before {{
            position: absolute;
            top: .7rem;
            left: .75rem;
            display: grid;
            place-items: center;
            width: 43px;
            height: 43px;
            border-radius: 50%;
            background: #dcecff;
            color: #0876c4;
            font-size: 1.35rem;
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-0 [data-testid="stMetricLabel"]::before {{ content: "▤"; }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-1 [data-testid="stMetricLabel"]::before {{
            content: "✓";
            background: #d7f2e5;
            color: #12865c;
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-2 [data-testid="stMetricLabel"]::before {{
            content: "♧";
            background: #e7e4ff;
            color: #6e5ad1;
        }}
        body:has(.st-key-sales-dashboard) .st-key-dashboard-metric-3 [data-testid="stMetricLabel"]::before {{
            content: "▣";
            background: #ffecd2;
            color: #d87509;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-info"] {{
            margin: -.3rem 0 .65rem;
            padding: .4rem .7rem;
            border: 1px solid #d4e4f6;
            border-radius: 8px;
            background: #e8f2ff;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-info"] [data-testid="stCaptionContainer"] {{
            color: #24466f;
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-chart-card"],
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-recent-card"] {{
            padding: .25rem .7rem .65rem;
            border-color: #d9e3f0;
            border-radius: 12px;
            background: rgba(255,255,255,.94);
            box-shadow: 0 3px 10px rgba(44, 75, 119, .06);
        }}
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-chart-card"] h3,
        body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-recent-card"] h3 {{
            color: #17365f;
            font-size: 1.08rem;
        }}
        .ui-dashboard-period {{
            display: inline-block;
            float: right;
            padding: .32rem .55rem;
            border: 1px solid #d8e2ee;
            border-radius: 7px;
            background: #fff;
            color: #415875;
            font-size: .72rem;
        }}
        body:has(.st-key-sales-dashboard) [data-testid="stDataFrame"] {{
            border: 1px solid #d4e0ef;
            border-radius: 9px;
            overflow: hidden;
        }}
        @media (max-width: 760px) {{
            body:has(.st-key-sales-dashboard) .block-container {{
                padding-left: .8rem;
                padding-right: .8rem;
            }}
            .ui-dashboard-hero {{
                margin-left: -.3rem;
                margin-right: -.3rem;
                background-position: 63% center;
            }}
            body:has(.st-key-sales-dashboard) [class*="st-key-dashboard-metric-"] {{
                min-height: 78px;
            }}
        }}
        </style>
        <div class="st-key-sales-dashboard"></div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="ui-dashboard-hero">
          <div class="ui-dashboard-title-row">
            <span class="ui-dashboard-title-icon" aria-hidden="true">
              <svg viewBox="0 0 32 32" fill="none">
                <path d="M5 26.5h23" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
                <rect x="7" y="16" width="4.3" height="9" rx="1" fill="currentColor" opacity=".58"/>
                <rect x="14" y="10" width="4.3" height="15" rx="1" fill="currentColor" opacity=".78"/>
                <rect x="21" y="5" width="4.3" height="20" rx="1" fill="currentColor"/>
              </svg>
            </span>
            <h1>Tổng quan kinh doanh</h1>
          </div>
          <p>Sales Freight Forwarding</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_values = [
        ("Tổng báo giá", len(quote_details)),
        ("Cần hoàn thiện", needs_review),
        ("Khách hàng", len(customer_names)),
        ("Cập nhật tháng này", active_month_updates),
    ]
    for row_start in (0, 2):
        metric_columns = st.columns(2)
        for offset, (column, (label, value)) in enumerate(
            zip(metric_columns, metric_values[row_start:row_start + 2]),
            start=row_start,
        ):
            with column:
                with st.container(key=f"dashboard-metric-{offset}"):
                    st.metric(label, value)

    with st.container(key="dashboard-info"):
        st.caption(
            "Báo giá cần hoàn thiện là các bản lưu chưa qua đủ kiểm tra dữ liệu xuất PDF. "
            "Hệ thống hiện chưa ghi nhận trạng thái đã gửi hoặc đồng ý từ khách hàng."
        )
    month_labels = pd.date_range(end=month_start, periods=6, freq="MS")
    update_series = pd.Series(updated_dates).dropna()
    monthly_counts = [
        int(
            (
                (update_series >= month)
                & (update_series < month + pd.offsets.MonthBegin(1))
            ).sum()
        )
        for month in month_labels
    ]
    monthly_data = pd.DataFrame(
        {
            "Tháng": [month.strftime("%m/%Y") for month in month_labels],
            "Số báo giá cập nhật": monthly_counts,
        }
    )
    with st.container(key="dashboard-chart-card", border=True):
        chart_title, chart_period = st.columns([5, 1])
        chart_title.markdown(
            '<div class="ui-dashboard-section-heading">'
            '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
            '<path d="M4 20V11h4v9M10 20V5h4v15M16 20V8h4v12" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg>Hoạt động báo giá</div>',
            unsafe_allow_html=True,
        )
        chart_period.markdown(
            '<div class="ui-dashboard-period">▣ &nbsp;6 tháng gần nhất</div>',
            unsafe_allow_html=True,
        )
        chart_spec = {
            "mark": {
                "type": "bar",
                "color": "#1684cf",
                "cornerRadiusTopLeft": 5,
                "cornerRadiusTopRight": 5,
            },
            "encoding": {
                "x": {
                    "field": "Tháng",
                    "type": "nominal",
                    "sort": monthly_data["Tháng"].tolist(),
                    "axis": {
                        "title": None,
                        "labelAngle": 0,
                        "labelColor": "#526782",
                        "labelFontSize": 11,
                        "labelPadding": 9,
                        "tickSize": 0,
                        "domain": False,
                    },
                },
                "y": {
                    "field": "Số báo giá cập nhật",
                    "type": "quantitative",
                    "scale": {"zero": True},
                    "axis": {
                        "title": None,
                        "labelColor": "#71819a",
                        "labelFontSize": 10,
                        "gridColor": "#e9eff7",
                        "tickSize": 0,
                        "domain": False,
                        "format": "d",
                    },
                },
                "tooltip": [
                    {"field": "Tháng", "type": "nominal", "title": "Tháng"},
                    {
                        "field": "Số báo giá cập nhật",
                        "type": "quantitative",
                        "title": "Báo giá cập nhật",
                        "format": "d",
                    },
                ],
            },
            "height": 230,
            "config": {"view": {"stroke": None}},
        }
        st.vega_lite_chart(monthly_data, chart_spec, width="stretch")
        st.caption(
            "Biểu đồ tính theo lần cập nhật gần nhất của mỗi báo giá, không phải "
            "ngày gửi khách hàng."
        )

    with st.container(key="dashboard-recent-card", border=True):
        st.markdown(
            '<div class="ui-dashboard-section-heading">'
            '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
            '<path d="M6 4.5h12a1.5 1.5 0 0 1 1.5 1.5v13L12 16l-7.5 3V6A1.5 '
            '1.5 0 0 1 6 4.5Z" stroke="currentColor" stroke-width="1.8" '
            'stroke-linejoin="round"/><path d="M8.5 8h7M8.5 11h7" '
            'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
            '</svg>Báo giá gần đây</div>',
            unsafe_allow_html=True,
        )
        if quote_details:
            recent_rows = []
            search_query = st.session_state.get("dashboard-search", "")
            query = str(search_query or "").strip().casefold()
            matches = [
                (summary, detail)
                for summary, detail in quote_details
                if not query
                or query in str(summary["quote_number"]).casefold()
                or query in str(summary["customer"] or "").casefold()
            ]
            for summary, detail in matches[:8]:
                errors = _quote_pdf_errors(detail)
                recent_rows.append(
                    {
                        "Mã báo giá": summary["quote_number"],
                        "Khách hàng": summary["customer"] or "Chưa điền",
                        "Trạng thái dữ liệu": ("Cần hoàn thiện" if errors else "Đủ dữ liệu PDF"),
                        "Cập nhật": summary["updated_at"],
                        "Has errors": bool(errors),
                    }
                )
            if recent_rows:
                table_rows = []
                for row in recent_rows:
                    status_class = (
                        "ui-dashboard-status"
                        if row["Has errors"]
                        else "ui-dashboard-status ui-dashboard-status-ready"
                    )
                    table_rows.append(
                        "<tr>"
                        f'<td><strong>{escape(str(row["Mã báo giá"]))}</strong></td>'
                        f'<td>{escape(str(row["Khách hàng"]))}</td>'
                        f'<td><span class="{status_class}">'
                        f'{escape(str(row["Trạng thái dữ liệu"]))}</span></td>'
                        f'<td>{escape(str(row["Cập nhật"]))}</td>'
                        "</tr>"
                    )
                st.markdown(
                    '<table class="ui-dashboard-recent-table">'
                    "<thead><tr><th>Mã báo giá</th><th>Khách hàng</th>"
                    "<th>Trạng thái dữ liệu</th><th>Cập nhật</th></tr></thead>"
                    f'<tbody>{"".join(table_rows)}</tbody></table>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="ui-dashboard-no-results">'
                    "Không tìm thấy báo giá hoặc khách hàng phù hợp.</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Bạn chưa có báo giá nào. Tạo báo giá đầu tiên để bắt đầu.")

        st.button(
            "＋ TẠO BÁO GIÁ",
            type="primary",
            key="dashboard_new_quote",
            on_click=_open_quote_workspace,
        )


def _open_quote_workspace():
    st.session_state["sales_navigation"] = "quotes"


def _reset_history_page():
    st.session_state["history_page_number"] = 1


def _toggle_history_advanced_filters():
    st.session_state["history_advanced_filters"] = not st.session_state.get(
        "history_advanced_filters", False
    )
    _reset_history_page()


def _toggle_history_page_selection():
    quote_ids = st.session_state.get("history_page_quote_ids", [])
    select_all = not all(
        st.session_state.get(f"history_selected_{quote_id}", False)
        for quote_id in quote_ids
    )
    for quote_id in quote_ids:
        st.session_state[f"history_selected_{quote_id}"] = select_all


def _previous_history_page():
    st.session_state["history_page_number"] = max(
        1, st.session_state.get("history_page_number", 1) - 1
    )


def _next_history_page():
    current = st.session_state.get("history_page_number", 1)
    last = st.session_state.get("history_page_count", 1)
    st.session_state["history_page_number"] = min(current + 1, last)


def _sales_history_page(user):
    _sales_hero(
        "My quotations",
        "Báo giá của tôi",
        "Tìm kiếm, lọc và quản lý các báo giá được lưu trong tài khoản của bạn.",
    )
    quotes = security_store.list_quotes(user["id"])
    quote_rows = []
    for quote in quotes:
        detail = security_store.get_quote(user["id"], quote["id"])
        if detail is None:
            continue
        needs_review = bool(_quote_pdf_errors(detail))
        quote_rows.append(
            {
                **quote,
                "status": "Cần hoàn thiện" if needs_review else "Đủ dữ liệu PDF",
                "needs_review": needs_review,
                "updated_timestamp": pd.to_datetime(
                    quote["updated_at"], errors="coerce"
                ),
            }
        )

    filter_columns = st.columns([1.2, 1.2, .95])
    period = filter_columns[0].selectbox(
        "Thời gian",
        ["Tất cả thời gian", "30 ngày gần nhất", "90 ngày gần nhất", "Năm nay"],
        key="history_time_filter",
        on_change=_reset_history_page,
    )
    status_filter = filter_columns[1].selectbox(
        "Trạng thái",
        ["Tất cả trạng thái", "Cần hoàn thiện", "Đủ dữ liệu PDF"],
        key="history_status_filter",
        on_change=_reset_history_page,
    )
    filter_columns[2].button(
        "▽  Bộ lọc nâng cao",
        key="history_advanced_filter_button",
        on_click=_toggle_history_advanced_filters,
        width="stretch",
    )

    sort_order = "Mới cập nhật"
    start_date = end_date = None
    if st.session_state.get("history_advanced_filters", False):
        advanced_columns = st.columns([1, 1, 1.3])
        start_date = advanced_columns[0].date_input(
            "Cập nhật từ ngày",
            value=None,
            key="history_start_date",
            on_change=_reset_history_page,
        )
        end_date = advanced_columns[1].date_input(
            "Đến ngày",
            value=None,
            key="history_end_date",
            on_change=_reset_history_page,
        )
        sort_order = advanced_columns[2].selectbox(
            "Sắp xếp theo",
            ["Mới cập nhật", "Cũ nhất", "Mã báo giá A–Z", "Khách hàng A–Z"],
            key="history_sort_order",
            on_change=_reset_history_page,
        )
        if start_date and end_date and start_date > end_date:
            st.error("Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.")
            return

    query = str(st.session_state.get("dashboard-search", "")).strip().casefold()
    filtered_rows = []
    today = pd.Timestamp.now().normalize()
    if period == "30 ngày gần nhất":
        cutoff = today - pd.Timedelta(days=30)
    elif period == "90 ngày gần nhất":
        cutoff = today - pd.Timedelta(days=90)
    elif period == "Năm nay":
        cutoff = today.replace(month=1, day=1)
    else:
        cutoff = None
    for row in quote_rows:
        updated = row["updated_timestamp"]
        if query and (
            query not in str(row["quote_number"]).casefold()
            and query not in str(row["customer"] or "").casefold()
        ):
            continue
        if status_filter == "Cần hoàn thiện" and not row["needs_review"]:
            continue
        if status_filter == "Đủ dữ liệu PDF" and row["needs_review"]:
            continue
        if cutoff is not None and (
            pd.isna(updated) or updated < cutoff
        ):
            continue
        if start_date and (pd.isna(updated) or updated.date() < start_date):
            continue
        if end_date and (pd.isna(updated) or updated.date() > end_date):
            continue
        filtered_rows.append(row)

    if sort_order == "Cũ nhất":
        filtered_rows.sort(
            key=lambda row: (
                pd.isna(row["updated_timestamp"]),
                row["updated_timestamp"],
            )
        )
    elif sort_order == "Mã báo giá A–Z":
        filtered_rows.sort(key=lambda row: str(row["quote_number"]).casefold())
    elif sort_order == "Khách hàng A–Z":
        filtered_rows.sort(key=lambda row: str(row["customer"] or "").casefold())

    st.caption(
        f"Hiển thị {len(filtered_rows)} trên tổng số {len(quote_rows)} báo giá."
    )
    if not filtered_rows:
        if not quote_rows:
            st.info("Chưa có báo giá được lưu. Tạo báo giá đầu tiên để bắt đầu.")
        else:
            st.info("Không tìm thấy báo giá phù hợp với các bộ lọc đã chọn.")
        st.button(
            "＋ Tạo báo giá",
            type="primary",
            key="history_create_quote",
            on_click=_open_quote_workspace,
        )
        return

    page_size = st.selectbox(
        "Hiển thị mỗi trang",
        [5, 10, 20, 50],
        index=1,
        key="history_page_size",
        on_change=_reset_history_page,
    )
    page_count = max(1, (len(filtered_rows) + page_size - 1) // page_size)
    st.session_state["history_page_count"] = page_count
    current_page = min(
        max(1, st.session_state.get("history_page_number", 1)),
        page_count,
    )
    st.session_state["history_page_number"] = current_page
    page_start = (current_page - 1) * page_size
    visible_rows = filtered_rows[page_start:page_start + page_size]
    page_quote_ids = [row["id"] for row in visible_rows]
    st.session_state["history_page_quote_ids"] = page_quote_ids

    all_page_selected = bool(page_quote_ids) and all(
        st.session_state.get(f"history_selected_{quote_id}", False)
        for quote_id in page_quote_ids
    )
    st.button(
        "☐  Bỏ chọn trang" if all_page_selected else "☑  Chọn tất cả trên trang",
        key="history_select_all_page",
        on_click=_toggle_history_page_selection,
        disabled=not page_quote_ids,
    )

    header = st.columns([.38, 1.25, 1.8, 1.15, 1.25, .65, .6])
    for column, label in zip(
        header,
        ["", "Mã báo giá", "Khách hàng", "Trạng thái dữ liệu", "Cập nhật", "Mở", "Xóa"],
    ):
        column.markdown(
            f'<div class="ui-sales-history-header">{escape(label) or "&nbsp;"}</div>',
            unsafe_allow_html=True,
        )

    for row in visible_rows:
        columns = st.columns([.38, 1.25, 1.8, 1.15, 1.25, .65, .6])
        columns[0].checkbox(
            f'Chọn {row["quote_number"]}',
            key=f'history_selected_{row["id"]}',
            label_visibility="collapsed",
        )
        columns[1].markdown(
            f'<div class="ui-sales-history-row"><strong>'
            f'{escape(str(row["quote_number"]))}</strong></div>',
            unsafe_allow_html=True,
        )
        columns[2].markdown(
            f'<div class="ui-sales-history-row">'
            f'{escape(str(row["customer"] or "Chưa điền"))}</div>',
            unsafe_allow_html=True,
        )
        status_class = (
            "ui-sales-status ui-sales-status-review"
            if row["needs_review"]
            else "ui-sales-status ui-sales-status-ready"
        )
        columns[3].markdown(
            f'<div class="ui-sales-history-row"><span class="{status_class}">'
            f'<i class="ui-sales-status-dot"></i>{escape(row["status"])}</span></div>',
            unsafe_allow_html=True,
        )
        columns[4].markdown(
            f'<div class="ui-sales-history-row">'
            f'{escape(str(row["updated_at"]))}</div>',
            unsafe_allow_html=True,
        )
        if columns[5].button(
            "↗",
            key=f'history_open_{row["id"]}',
            help="Mở và tiếp tục chỉnh sửa báo giá",
            width="stretch",
        ):
            _load_saved_quote(user["id"], row["id"])
        if columns[6].button(
            "🗑",
            key=f'history_delete_{row["id"]}',
            help="Xóa báo giá",
            width="stretch",
        ):
            st.session_state["pending_quote_delete_ids"] = [row["id"]]
            st.rerun()

    selected_ids = [
        quote_id
        for quote_id in page_quote_ids
        if st.session_state.get(f"history_selected_{quote_id}", False)
    ]
    footer = st.columns([1.8, 1.2, 1.15, 1.2, .55, .8, .55])
    footer[0].caption(f"Đã chọn: {len(selected_ids)} báo giá")
    if footer[1].button(
        "📂 Mở báo giá đã chọn",
        type="primary",
        key="open_quote_from_history",
        disabled=len(selected_ids) != 1,
        width="stretch",
    ):
        _load_saved_quote(user["id"], selected_ids[0])
    if footer[2].button(
        "🗑 Xóa báo giá",
        key="request_quote_delete",
        disabled=not selected_ids,
        width="stretch",
    ):
        st.session_state["pending_quote_delete_ids"] = selected_ids
        st.rerun()
    footer[3].markdown(f"Trang {current_page} / {page_count}")
    footer[4].button(
        "‹",
        key="history_previous_page",
        disabled=current_page <= 1,
        on_click=_previous_history_page,
        help="Trang trước",
    )
    footer[5].selectbox(
        "Trang",
        range(1, page_count + 1),
        key="history_page_number",
        label_visibility="collapsed",
        format_func=lambda page: str(page),
    )
    footer[6].button(
        "›",
        key="history_next_page",
        disabled=current_page >= page_count,
        on_click=_next_history_page,
        help="Trang tiếp theo",
    )

    pending_ids = st.session_state.get("pending_quote_delete_ids", [])
    owned_ids = {row["id"] for row in quote_rows}
    pending_ids = [quote_id for quote_id in pending_ids if quote_id in owned_ids]
    if st.session_state.get("pending_quote_delete_ids") and not pending_ids:
        st.session_state.pop("pending_quote_delete_ids", None)
    if pending_ids:
        quote_numbers = {
            row["id"]: row["quote_number"] for row in quote_rows
        }
        st.warning(
            "Bạn sắp xóa vĩnh viễn "
            + ", ".join(quote_numbers[quote_id] for quote_id in pending_ids)
            + ". Thao tác này không thể hoàn tác."
        )
        confirm_column, cancel_column = st.columns(2)
        if confirm_column.button(
            "Xác nhận xóa",
            type="primary",
            key="confirm_quote_delete_button",
        ):
            try:
                deleted_ids = security_store.delete_quotes(user["id"], pending_ids)
            except security_store.DATABASE_ERRORS as error:
                st.error(f"Không thể xóa báo giá: {error}")
            else:
                if len(deleted_ids) != len(pending_ids):
                    st.error(
                        "Không xóa được toàn bộ báo giá đã chọn. "
                        "Danh sách đã được làm mới."
                    )
                    st.session_state.pop("pending_quote_delete_ids", None)
                else:
                    st.session_state.pop("pending_quote_delete_ids", None)
                    for quote_id in deleted_ids:
                        st.session_state.pop(f"history_selected_{quote_id}", None)
                    if st.session_state.get("current_quote_id") in deleted_ids:
                        _start_new_quote()
                    st.rerun()
        if cancel_column.button("Hủy", key="cancel_quote_delete"):
            st.session_state.pop("pending_quote_delete_ids", None)
            st.rerun()

    st.caption(
        "Trạng thái phản ánh mức độ đầy đủ dữ liệu; hệ thống hiện chưa ghi nhận "
        "việc gửi báo giá hoặc phản hồi của khách hàng."
    )


def _render_quote(user):
    _initialize_quote_state()

    current_fees = st.session_state["edited_fees"]
    if current_fees is None:
        current_fees = st.session_state["fees"]
    current_calculated, _, _ = calculate_quote(current_fees)
    current_fingerprint = _payload_fingerprint(
        _quote_payload(current_calculated)
    )
    saved_current = (
        st.session_state["current_quote_id"] is not None
        and st.session_state["saved_quote_fingerprint"] == current_fingerprint
    )
    confirmed_current = (
        saved_current
        and st.session_state["confirmed_pdf_fingerprint"] == current_fingerprint
    )
    fields = _quote_fields()
    _sales_hero(
        "Sales workspace",
        "Báo giá hàng xuất",
        "Tải bảng giá Pricing để AI tạo bản nháp; rà soát thông tin và xác nhận trước khi xuất PDF.",
    )
    quote_actions = st.columns([5, 1])
    quote_actions[0].caption(
        f'Tuyến vận chuyển: {fields["quote_pol"] or "POL chưa xác định"} → '
        f'{fields["quote_pod"] or "POD chưa xác định"}'
    )
    if quote_actions[1].button(
        "↻  Làm mới",
        key="reset_quote_workspace",
        help="Tạo một bản nháp mới; bản đã lưu vẫn còn trong danh sách.",
        width="stretch",
    ):
        _start_new_quote()

    step_columns = st.columns(3)
    step_labels = [
        (
            "1 · Đọc bảng Pricing",
            "Đã đọc file" if st.session_state["ai_source_name"] else "Chưa đọc file",
            "📄",
        ),
        (
            "2 · Hoàn thiện dữ liệu",
            "Đã lưu bản nháp" if saved_current else "Chưa lưu hoặc có thay đổi",
            "✎",
        ),
        (
            "3 · Kiểm tra & xuất PDF",
            "Đã xác nhận" if confirmed_current else "Chưa xác nhận",
            "📑",
        ),
    ]
    for column, (title, state, icon) in zip(step_columns, step_labels):
        column.markdown(
            f'<div class="ui-step-card"><span class="ui-step-icon">{icon}</span>'
            f'<div><strong>{escape(title)}</strong>'
            f'<span>{escape(state)}</span></div></div>',
            unsafe_allow_html=True,
        )
    ai_tab, quote_tab, preview_tab = st.tabs(
        [
            "🤖 Tạo từ bảng Pricing",
            "📋 Hoàn thiện báo giá",
            "📄 Xem trước & PDF",
        ]
    )

    with ai_tab:
        st.subheader("Tạo báo giá từ bảng giá Pricing")
        st.caption(
            "Tải file Pricing lên để AI đọc và tự tạo một báo giá nháp đã lưu. "
            "AI chỉ điền dữ liệu có trong file; thông tin thiếu để trống. "
            "Sales rà soát, nhập Profit và xác nhận trước khi xuất PDF."
        )
        uploaded = st.file_uploader(
            "Tải Excel, CSV, PDF, TXT hoặc ảnh",
            type=["xlsx", "xls", "csv", "pdf", "txt", "png", "jpg", "jpeg", "webp"],
            key="pricing_upload",
        )
        ai_config = app_config.ai_settings()
        provider = ai_config["provider"]
        uploaded_data = uploaded.getvalue() if uploaded is not None else None
        external_ai_consent = False
        if uploaded is not None:
            st.write(
                "Tệp đã tải:",
                uploaded.name,
                f"· {uploaded.size / 1024:,.1f} KB · Sẵn sàng đọc",
            )
            if provider == "openai_compatible":
                st.warning(
                    "Tệp Pricing sẽ được gửi đến AI Cloud đã cấu hình. "
                    "Chỉ tiếp tục nếu công ty cho phép chia sẻ dữ liệu này."
                )
                upload_fingerprint = hashlib.sha256(uploaded_data).hexdigest()
                external_ai_consent = st.checkbox(
                    "Tôi cho phép gửi tệp này tới AI Cloud để trích xuất.",
                    key=f"external_ai_consent_{upload_fingerprint}",
                )
        if provider not in ("ollama", "openai_compatible"):
            st.error("AI provider trong Secrets chưa được hỗ trợ.")
        if st.button(
            "🤖 ĐỌC FILE VÀ TẠO BÁO GIÁ NHÁP",
            disabled=(
                uploaded is None
                or provider not in ("ollama", "openai_compatible")
                or provider == "openai_compatible" and not external_ai_consent
            ),
        ):
            try:
                source_text, source_images, source_warnings = read_source(
                    uploaded.name, uploaded_data
                )
                if provider == "ollama":
                    model = security_store.get_ollama_model()
                    if source_images and not any(
                        token in model.lower()
                        for token in ("vl", "vision", "llava", "gemma3")
                    ):
                        st.error(
                            "Tệp ảnh/PDF scan cần mô hình thị giác, ví dụ "
                            "qwen2.5vl:3b."
                        )
                        result = None
                    else:
                        with st.spinner(
                            "Ollama đang đọc bảng giá trên máy chủ..."
                        ):
                            result = ask_ollama(
                                source_text,
                                source_images,
                                model=model.strip(),
                                base_url=(
                                    ai_config["base_url"]
                                    or "http://127.0.0.1:11434"
                                ),
                            )
                else:
                    with st.spinner("AI Cloud đang đọc bảng giá..."):
                        result = ask_openai_compatible(
                            source_text,
                            source_images,
                            model=ai_config["model"],
                            base_url=ai_config["base_url"],
                            api_key=ai_config["api_key"],
                        )
                if result is not None:
                    candidate, warnings = normalize(result)
                    if candidate.empty:
                        raise ValueError(
                            "AI không trích xuất được khoản phí nào; "
                            "chưa tạo báo giá."
                        )
                    quote_number = _create_ai_quote_draft(
                        user,
                        result,
                        candidate,
                        uploaded.name,
                        source_warnings + warnings,
                    )
                    st.success(
                        f"Đã tạo và tự động lưu báo giá nháp {quote_number}. "
                        f"AI nhận diện {len(candidate)} khoản phí. "
                        "Mở tab “Hoàn thiện báo giá” để rà soát."
                    )
            except (
                ImportError,
                OSError,
                RuntimeError,
                ValueError,
                *security_store.DATABASE_ERRORS,
            ) as error:
                st.error(f"Không thể đọc bảng giá: {error}")

        if st.session_state["ai_source_name"]:
            st.success(
                f'AI đã nhận diện {len(st.session_state["fees"])} khoản phí '
                "và lưu vào bản nháp."
            )
            st.caption(f'Tệp nguồn: {st.session_state["ai_source_name"]}')
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Trường": label,
                            "AI nhận diện": str(
                                st.session_state.get("ai_metadata", {}).get(key)
                                or ""
                            ),
                        }
                        for label, key in (
                            ("Khách hàng", "customer"),
                            ("POL", "pol"),
                            ("POD", "pod"),
                            ("Incoterm", "incoterm"),
                            ("Hiệu lực đến", "valid_until"),
                            ("Loại container", "container_type"),
                            ("Số lượng container", "container_quantity"),
                            ("Hãng tàu", "carrier"),
                        )
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            for warning in st.session_state.get("ai_warnings", []):
                st.warning(warning)

    with quote_tab:
        st.subheader("Thông tin lô hàng")
        with st.container(border=True):
            first, second, third = st.columns(3)
            with first:
                st.text_input("Khách hàng", key="quote_customer")
                st.text_input("Cảng xuất (POL)", key="quote_pol")
                st.selectbox(
                    "Incoterms",
                    [
                        "",
                        "EXW",
                        "FCA",
                        "FAS",
                        "FOB",
                        "CFR",
                        "CIF",
                        "CPT",
                        "CIP",
                        "DAP",
                        "DPU",
                        "DDP",
                    ],
                    format_func=lambda value: value or "Chưa xác định",
                    key="quote_incoterm",
                )
                st.selectbox(
                    "Loại container",
                    ["", "20GP", "40GP", "40HC", "20RF", "40RF"],
                    format_func=lambda value: value or "Chưa xác định",
                    key="quote_container",
                )
            with second:
                st.text_input("Cảng đến (POD)", key="quote_pod")
                st.text_input("Hàng hóa", key="quote_commodity")
                st.number_input(
                    "Số container",
                    min_value=1,
                    value=None,
                    step=1,
                    key="quote_qty",
                )
                st.text_input("Hãng tàu (nếu đã xác nhận)", key="quote_carrier")
            with third:
                st.date_input("Ngày báo giá", key="quote_date")
                st.date_input(
                    "Hiệu lực đến", value=None, key="quote_valid_until"
                )
                st.text_input("Mã báo giá", key="quote_number")
                st.text_input("Nhân viên Sales", key="quote_sales_name")
                st.text_input("Email Sales", key="quote_sales_email")
                st.text_input("Điện thoại Sales", key="quote_sales_phone")
        route = (
            f'{fields["quote_pol"] or "POL chưa xác định"} → '
            f'{fields["quote_pod"] or "POD chưa xác định"}'
        )
        container = (
            f'{fields["quote_qty"]} × {fields["quote_container"]}'
            if fields["quote_qty"] and fields["quote_container"]
            else "Chưa đủ số lượng/loại container"
        )
        incoterm = fields["quote_incoterm"] or "Incoterm chưa xác định"
        st.info(f"{route} · {container} · {incoterm} · Sea FCL/FCL")
        if st.session_state["ai_source_name"]:
            st.success(
                f'Báo giá nháp được AI tạo và lưu từ '
                f'{st.session_state["ai_source_name"]}. '
                "Các khoản phí đọc được đã nằm trong bảng bên dưới."
            )
        if st.session_state["current_quote_id"] is None:
            st.caption("Báo giá nháp mới · Có thể lưu trước khi đủ thông tin.")

        st.subheader("Bảng phí")
        st.caption(
            "Profit là số USD Sales cộng cho mỗi đơn vị phí: "
            "Giá bán/đơn vị = Giá mua/đơn vị + Profit. "
            "Ví dụ: 300 + 130 = 430 USD. Profit từ file AI để trống để Sales "
            "tự quyết định; không tự gán mức mặc định."
        )
        if st.session_state["fees"].empty:
            source = pd.DataFrame(columns=FEE_COLUMNS)
        else:
            source = st.session_state["fees"].copy()
        for column, default in (
            ("Nhóm phí", ""),
            ("Khoản phí", ""),
            ("Đơn vị", ""),
            ("Số lượng", 1.0),
            ("Giá mua (USD)", 0.0),
            ("Profit (USD)", 0.0),
            ("Giá bán (USD)", 0.0),
            ("Ghi chú", ""),
            ("Hiển thị PDF", False),
        ):
            if column not in source.columns:
                source[column] = default
        hidden_rows = internal_fee_mask(source)
        billable_source = source.loc[~hidden_rows].copy()
        internal_source = source.loc[hidden_rows].copy()

        allocation_rows = billable_source[
            billable_source["Khoản phí"].fillna("").astype(str).str.strip().ne("")
        ]
        allocation_costs = pd.to_numeric(
            allocation_rows["Giá mua (USD)"], errors="coerce"
        )
        allocation_quantities = pd.to_numeric(
            allocation_rows["Số lượng"], errors="coerce"
        )
        allocation_incomplete = (
            allocation_costs.isna()
            | allocation_costs.lt(0)
            | allocation_quantities.isna()
            | allocation_quantities.le(0)
        )
        st.markdown("**Phân bổ Profit nhanh**")
        if allocation_rows.empty:
            st.info("Thêm ít nhất một khoản phí trước khi phân bổ Profit.")
        elif allocation_incomplete.any():
            st.warning(
                f"Còn {int(allocation_incomplete.sum())} khoản phí thiếu số lượng "
                "hoặc giá mua hợp lệ. Hãy điền các ô này; hệ thống sẽ tự phân "
                "bổ mức Profit đang chọn khi đủ dữ liệu."
            )
        st.slider(
            "Markup trên giá mua (%)",
            min_value=0.0,
            max_value=100.0,
            step=0.1,
            format="%.1f%%",
            key="profit_markup_percent",
            help=(
                "Profit mục tiêu = tỷ lệ markup × tổng giá mua. "
                "Hệ thống chia phần Profit đó theo tỷ trọng giá mua × số lượng."
            ),
        )
        st.caption(
            "Ví dụ 10%: giá mua 300 USD → Profit 30 USD → giá bán 330 USD. "
            "Tỷ lệ được áp dụng theo giá mua; phí nội bộ không tham gia. "
            "Sau khi phân bổ, bạn vẫn có thể chỉnh Profit riêng từng dòng."
        )
        if st.session_state.get("profit_allocation_message"):
            st.info(st.session_state["profit_allocation_message"])

        st.markdown("**Các khoản phí bán cho khách**")
        edited_billable = st.data_editor(
            billable_source[
                [
                    "Nhóm phí",
                    "Khoản phí",
                    "Đơn vị",
                    "Số lượng",
                    "Giá mua (USD)",
                    "Profit (USD)",
                    "Ghi chú",
                    "Hiển thị PDF",
                ]
            ],
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=f'billable_editor_{st.session_state["editor_version"]}',
            column_config={
                "Nhóm phí": st.column_config.SelectboxColumn(
                    "Nhóm phí", options=PUBLIC_GROUPS, required=True
                ),
                "Số lượng": st.column_config.NumberColumn(
                    "Số lượng", min_value=0, format="%.3f"
                ),
                "Giá mua (USD)": st.column_config.NumberColumn(
                    "Giá mua/đơn vị (USD)", min_value=0, format="%.2f"
                ),
                "Profit (USD)": st.column_config.NumberColumn(
                    "Profit/đơn vị (USD)", format="%.2f", step=1.0
                ),
                "Profit (USD)": st.column_config.NumberColumn(
                    "Profit/đơn vị (USD)", format="%.2f"
                ),
                "Hiển thị PDF": st.column_config.CheckboxColumn(
                    "Đưa vào PDF khách"
                ),
            },
        )
        st.markdown("**Chi phí nội bộ (không xuất cho khách)**")
        if not internal_source.empty:
            internal_source["Nhóm phí"] = "CHI PHÍ NỘI BỘ"
        edited_internal = st.data_editor(
            internal_source[
                ["Khoản phí", "Đơn vị", "Số lượng", "Giá mua (USD)", "Ghi chú"]
            ],
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=f'internal_editor_{st.session_state["editor_version"]}',
            column_config={
                "Số lượng": st.column_config.NumberColumn(
                    "Số lượng", min_value=0, format="%.3f"
                ),
                "Giá mua (USD)": st.column_config.NumberColumn(
                    "Giá mua/đơn vị (USD)", min_value=0, format="%.2f"
                ),
            },
        )
        edited_billable = edited_billable.copy()
        edited_internal = edited_internal.copy()
        edited_internal["Nhóm phí"] = "CHI PHÍ NỘI BỘ"
        edited_internal["Profit (USD)"] = -pd.to_numeric(
            edited_internal.get("Giá mua (USD)", pd.Series(dtype=float)),
            errors="coerce",
        ).fillna(0)
        edited_internal["Giá bán (USD)"] = 0.0
        edited_internal["Hiển thị PDF"] = False
        combined = pd.concat(
            [edited_billable, edited_internal], ignore_index=True, sort=False
        )
        markup_percent = float(st.session_state["profit_markup_percent"])
        last_markup_percent = float(
            st.session_state["profit_last_applied_percent"]
        )
        eligible_rows = (
            ~internal_fee_mask(combined)
            & combined["Khoản phí"].str.strip().ne("")
        )
        combined_allocation_rows = combined.loc[eligible_rows]
        combined_costs = pd.to_numeric(
            combined_allocation_rows["Giá mua (USD)"], errors="coerce"
        )
        combined_quantities = pd.to_numeric(
            combined_allocation_rows["Số lượng"], errors="coerce"
        )
        combined_incomplete = (
            combined_costs.isna()
            | combined_costs.lt(0)
            | combined_quantities.isna()
            | combined_quantities.le(0)
        )
        combined_allocation_ready = (
            not combined_allocation_rows.empty and not combined_incomplete.any()
        )
        if markup_percent != last_markup_percent and (
            combined_allocation_ready or markup_percent == 0
        ):
            total_cost_basis = float(
                (
                    pd.to_numeric(
                        combined["Giá mua (USD)"], errors="coerce"
                    )
                    * pd.to_numeric(combined["Số lượng"], errors="coerce")
                )[eligible_rows].sum()
            )
            allocation_target = total_cost_basis * markup_percent / 100
            try:
                allocated_fees = allocate_profit_by_cost(
                    combined, allocation_target
                )
            except ValueError as error:
                st.session_state["profit_allocation_error"] = str(error)
                st.session_state.pop("profit_allocation_message", None)
            else:
                allocated_fees, _, _ = calculate_quote(allocated_fees)
                st.session_state["fees"] = allocated_fees[FEE_COLUMNS].copy()
                st.session_state["edited_fees"] = allocated_fees[
                    FEE_COLUMNS
                ].copy()
                st.session_state["profit_last_applied_percent"] = (
                    markup_percent
                )
                st.session_state.pop("profit_allocation_error", None)
                allocated_mask = (
                    ~internal_fee_mask(allocated_fees)
                    & allocated_fees["Khoản phí"].str.strip().ne("")
                )
                allocated_profit = float(
                    (
                        pd.to_numeric(
                            allocated_fees["Profit (USD)"], errors="coerce"
                        ).fillna(0)
                        * pd.to_numeric(
                            allocated_fees["Số lượng"], errors="coerce"
                        ).fillna(0)
                    )[allocated_mask].sum()
                )
                st.session_state["profit_allocation_message"] = (
                    f"Mức markup {markup_percent:.1f}% (mục tiêu "
                    f"{allocation_target:,.2f} USD) đã được phân bổ; "
                    f"tổng thực tế sau khi "
                    f"làm tròn là {allocated_profit:,.2f} USD. "
                    "Bạn có thể chỉnh riêng từng dòng."
                )
                st.session_state["editor_version"] += 1
                st.session_state["confirmed_pdf_fingerprint"] = None
                st.rerun()
        elif markup_percent != last_markup_percent:
            if combined_allocation_rows.empty:
                st.warning(
                    "Thêm ít nhất một khoản phí trước khi phân bổ markup."
                )
            else:
                st.warning(
                    f"Còn {int(combined_incomplete.sum())} khoản phí thiếu số "
                    "lượng hoặc giá mua hợp lệ. Hãy điền các ô này; hệ thống "
                    "sẽ tự phân bổ mức markup đang chọn khi đủ dữ liệu."
                )

        if st.session_state.get("profit_allocation_error"):
            st.warning(st.session_state["profit_allocation_error"])

        calculated, public_rows, totals = calculate_quote(combined)
        st.session_state["edited_fees"] = calculated[FEE_COLUMNS].copy()

        if not public_rows.empty:
            st.markdown("**Giá bán được tính tự động từ Profit đã nhập**")
            st.caption(
                "Nhập Profit một lần; giá bán/đơn vị và tổng tiền được tính lại "
                "ngay. Lưu bản nháp để ghi nhận thay đổi."
            )
            st.dataframe(
                public_rows[
                    [
                        "Nhóm phí",
                        "Khoản phí",
                        "Đơn vị",
                        "Số lượng",
                        "Giá mua (USD)",
                        "Profit (USD)",
                        "Giá bán (USD)",
                        "Thành tiền bán",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )

        current_fingerprint = _payload_fingerprint(
            _quote_payload(calculated)
        )
        if st.session_state["current_quote_id"] is None:
            st.caption("Trạng thái: bản nháp mới, chưa lưu.")
        elif (
            st.session_state["saved_quote_fingerprint"]
            == current_fingerprint
        ):
            st.caption("Trạng thái: đã lưu, chưa có thay đổi mới.")
        else:
            st.warning("Có thay đổi chưa lưu trong báo giá.")

        unresolved_group = (
            ~calculated["Nhóm phí"].isin(GROUPS)
            | calculated["Nhóm phí"].eq("CHƯA PHÂN LOẠI")
        )
        unclassified_count = int(
            (
                unresolved_group
                & calculated["Khoản phí"].str.strip().ne("")
            ).sum()
        )
        if unclassified_count:
            st.warning(
                f"Còn {unclassified_count} khoản chưa phân loại hoặc thiếu nhóm. "
                "Hãy phân loại hoặc xóa khoản không áp dụng trước khi xuất PDF."
            )
        missing_cost_inputs = calculated[
            ["Số lượng", "Giá mua (USD)"]
        ].isna().any().any()
        missing_profit_inputs = (
            not public_rows.empty
            and public_rows["Profit (USD)"].isna().any()
        )
        if missing_cost_inputs or missing_profit_inputs:
            st.warning(
                "Bảng phí còn dữ liệu chưa hoàn chỉnh; các tổng hiện tại chỉ "
                "mang tính tạm tính. Hãy bổ sung giá mua, số lượng và Profit."
            )
        missing_public_units = (
            not public_rows.empty
            and public_rows["Đơn vị"]
            .fillna("")
            .astype(str)
            .str.strip()
            .isin(("", "USD", "US$", "CHƯA RÕ"))
            .any()
        )
        if missing_public_units:
            st.warning(
                "Có khoản phí thiếu đơn vị tính hoặc đang dùng loại tiền làm "
                "đơn vị; hãy xác nhận đơn vị trước khi xuất PDF."
            )

        metric_cols = st.columns(4)
        metric_cols[0].metric(
            "Tổng giá mua nội bộ", f'{totals["total_cost"]:,.2f} USD'
        )
        metric_cols[1].metric(
            "Tổng giá bán khách", f'{totals["total_sell"]:,.2f} USD'
        )
        metric_cols[2].metric(
            "Lợi nhuận ròng dự kiến", f'{totals["net_profit"]:,.2f} USD'
        )
        metric_cols[3].metric(
            "Tỷ suất LN / giá bán", f'{totals["margin"]:.2f}%'
        )
        st.caption(
            "Lợi nhuận ròng = tổng giá bán khách − toàn bộ giá mua, "
            "bao gồm Commission và chi phí chưa đưa vào PDF. "
            "Tỷ suất chỉ là kết quả tham khảo; hệ thống không tự áp dụng Profit chung."
        )

        st.subheader("Ghi chú và phạm vi dịch vụ")
        st.text_area("Ghi chú gửi khách (mỗi dòng một ghi chú)", key="quote_notes")
        st.text_input("Phạm vi dịch vụ", key="quote_service_scope")
        if st.button(
            "💾 LƯU BẢN NHÁP",
            type="primary",
            key="save_quote",
        ):
            if not st.session_state["quote_number"].strip():
                st.error("Vui lòng nhập mã báo giá trước khi lưu.")
            else:
                try:
                    payload = _quote_payload(calculated)
                    saved_id = security_store.save_quote(
                        user["id"],
                        st.session_state["current_quote_id"],
                        st.session_state["quote_number"].strip(),
                        st.session_state["quote_customer"].strip(),
                        payload,
                    )
                except (
                    ValueError,
                    *security_store.DATABASE_ERRORS,
                    OSError,
                ) as error:
                    st.error(f"Không thể lưu báo giá: {error}")
                else:
                    st.session_state["current_quote_id"] = saved_id
                    st.session_state["saved_quote_fingerprint"] = (
                        _payload_fingerprint(payload)
                    )
                    st.session_state["confirmed_pdf_fingerprint"] = None
                    st.success(
                        "Bản nháp đã được lưu riêng cho tài khoản của bạn."
                    )
                    st.rerun()

    with preview_tab:
        fields = _quote_fields()
        preview_fees = st.session_state["edited_fees"]
        if preview_fees is None:
            preview_fees = st.session_state["fees"]
        calculated, public_rows, totals = calculate_quote(
            preview_fees
        )
        st.subheader("Bản xem trước báo giá khách hàng")
        st.caption(
            "Chỉ các dòng đã chọn hiển thị, được phân loại và có giá trị hợp lệ "
            "mới xuất hiện trong PDF."
        )
        if not public_rows.empty:
            st.dataframe(
                public_rows[
                    [
                        "Nhóm phí",
                        "Khoản phí",
                        "Đơn vị",
                        "Số lượng",
                        "Giá bán (USD)",
                        "Thành tiền bán",
                        "Ghi chú",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )
        errors = validate_pdf(fields, public_rows, calculated)
        current_fingerprint = _payload_fingerprint(_quote_payload(calculated))
        if st.session_state["current_quote_id"] is None:
            errors.append("Hãy lưu báo giá trước khi xác nhận và tải PDF.")
        elif (
            st.session_state["saved_quote_fingerprint"] != current_fingerprint
        ):
            errors.append("Báo giá có thay đổi chưa lưu. Hãy lưu trước khi xuất PDF.")
        for error in errors:
            st.error(error)
        if not errors:
            if (
                st.session_state["confirmed_pdf_fingerprint"]
                != current_fingerprint
            ):
                st.info(
                    "Hãy kiểm tra toàn bộ thông tin và xác nhận nội dung báo giá. "
                    "Mọi thay đổi sau xác nhận sẽ yêu cầu xác nhận lại."
                )
                if st.button(
                    "✅ XÁC NHẬN NỘI DUNG BÁO GIÁ",
                    type="primary",
                    key="confirm_customer_quote",
                ):
                    st.session_state["confirmed_pdf_fingerprint"] = (
                        current_fingerprint
                    )
                    st.rerun()
            else:
                pdf_data = create_pdf(public_rows, fields, totals)
                filename = re.sub(
                    r'[<>:"/\\|?*]+', "-", fields["quote_number"]
                ).strip("-")
                st.download_button(
                    "⬇ TẢI BÁO GIÁ PDF",
                    data=pdf_data,
                    file_name=f"Export_Quotation_{filename or 'quote'}.pdf",
                    mime="application/pdf",
                    type="primary",
                )
                st.success(
                    "PDF không chứa giá mua, Profit nội bộ hoặc Commission."
                )

def _bootstrap_admin_from_secrets():
    if security_store.has_admin():
        return
    credentials = app_config.bootstrap_admin()
    if credentials is None:
        return
    security_store.create_bootstrap_admin(
        credentials["username"],
        credentials["display_name"],
        credentials["password"],
        must_change_password=True,
    )


def main():
    try:
        security_store.initialize()
        _bootstrap_admin_from_secrets()
        except (ValueError, RuntimeError, *security_store.DATABASE_ERRORS) as e:
        st.error(
            "Không thể khởi tạo cơ sở dữ liệu hoặc tài khoản quản trị. "
            "Hãy kiểm tra cấu hình triển khai và quyền kết nối."
        )
        st.exception(e)
        st.stop()
    apply_styles()
    if not secure_transport():
        st.error(
            "Đăng nhập qua mạng chỉ được phép bằng HTTPS. Hãy cấu hình HTTPS "
            "trên reverse proxy nội bộ và không mở trực tiếp cổng Streamlit "
            "ra mạng."
        )
        st.stop()

    user_id = st.session_state.get("auth_user_id")
    user = security_store.get_user(user_id) if user_id is not None else None
    if user_id is not None and (user is None or not user["active"]):
        st.session_state.clear()
        user = None
    if user is None:
        _show_login()
        return
    st.session_state.pop("login_username", None)
    st.session_state.pop("login_password", None)
    if st.session_state.pop("clear_password_inputs", False):
        for key in (
            "new_password",
            "new_password_confirmation",
            "admin_create_password",
            "admin_create_password_confirmation",
            "admin_reset_password",
            "admin_reset_password_confirmation",
        ):
            st.session_state.pop(key, None)
    last_activity = st.session_state.get("auth_last_activity")
    now = time.time()
    if last_activity is not None and now - last_activity > SESSION_IDLE_TIMEOUT:
        st.session_state.clear()
        st.info("Phiên đăng nhập đã hết hạn do không hoạt động.")
        _show_login()
        return
    st.session_state["auth_last_activity"] = now

    if user["must_change_password"]:
        _sidebar(user, show_navigation=False)
        st.markdown(
            '<div class="ui-hero"><div class="ui-eyebrow">Account security</div>'
            "<h1>Đổi mật khẩu lần đầu</h1>"
            "<p>Hoàn tất bước này để truy cập hệ thống báo giá.</p></div>",
            unsafe_allow_html=True,
        )
        _change_password_form(user)
        return

    if user["role"] == "sales":
        _initialize_quote_state()
        if st.session_state.pop("open_quote_workspace", False):
            st.session_state["sales_navigation"] = "quotes"
    active_page = _sidebar(user)
    if user["role"] == "admin":
        _admin_topbar(user)
        if active_page == "settings":
            _admin_settings_page(user)
        else:
            _admin_page(user)
    else:
        _sales_topbar(user)
        if active_page == "account":
            _sales_account_page(user)
        elif active_page == "history":
            _sales_history_page(user)
        elif active_page == "dashboard":
            _sales_dashboard(user)
        else:
            _render_quote(user)
        _apply_sales_theme()


main()
