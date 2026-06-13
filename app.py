# app.py
# -*- coding: utf-8 -*-
import os
import re
import base64
import mimetypes
import pathlib
import sqlite3
from datetime import datetime, date, timedelta
from io import BytesIO
from typing import Dict, Any, Optional, List, Tuple
import unicodedata

import altair as alt
import pandas as pd
import streamlit as st

# ======================
# Cấu hình chung
# ======================
DB_PATH = "ward_tracker.db"
DUTY_DIR = "uploads/duty"
PATIENT_UPLOAD_DIR = "uploads/patients"
DATE_FMT = "%Y-%m-%d"
APP_PASSWORD = os.getenv("APP_PASSWORD", "")  # để trống thì tắt password
st.set_page_config(page_title="Bác sĩ Trực tuyến - Theo dõi bệnh nhân", layout="wide", page_icon="🩺")

# ======================
# CSS tinh gọn giao diện
# ======================
CUSTOM_CSS = """
<style>
:root{--accent:#0d9488;--muted:#6b7280;--card:#ffffff;--bg:#f8fafc}
body{background:var(--bg)}
.kpi{display:flex;align-items:center;gap:12px;padding:14px;border-radius:12px;background:linear-gradient(180deg, rgba(255,255,255,0.9), rgba(250,250,250,0.9));box-shadow:0 6px 20px rgba(13, 20, 25, 0.06);border:1px solid rgba(0,0,0,0.04)}
.kpi .icon{width:44px;height:44px;border-radius:10px;background:var(--accent);display:flex;align-items:center;justify-content:center;color:white;font-weight:700}
.kpi h3{margin:0;font-size:0.85rem;color:var(--muted)}
.kpi .v{font-weight:700;font-size:1.45rem;margin-top:2px}
.header-card{padding:16px;border-radius:12px;background:linear-gradient(90deg,#f1f5f9,#ffffff);box-shadow:0 6px 18px rgba(13,20,25,0.04);margin-bottom:12px}
.quick-actions{display:flex;gap:10px;flex-wrap:wrap}
.quick-btn{background:var(--card);border-radius:10px;padding:10px 12px;border:1px solid rgba(0,0,0,0.06);cursor:pointer}
.small{font-size:0.9rem;color:var(--muted)}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:0.75rem;border:1px solid rgba(0,0,0,0.08)}
.badge.ok{background:#e8f5e9}
.badge.warn{background:#fff8e1}
.badge.danger{background:#ffebee}
.embed{width:100%;height:720px;border:1px solid rgba(0,0,0,0.08);border-radius:12px;overflow:hidden}
.morning-row{padding:10px 0;border-bottom:1px solid rgba(0,0,0,0.06)}
.patient-chip{display:inline-block;padding:3px 8px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:0.78rem;margin-right:6px}
.muted-line{color:var(--muted);font-size:0.9rem}

/* Responsive: tối ưu cho điện thoại */
@media (max-width: 800px) {
    .kpi{flex-direction:row;gap:10px;padding:10px}
    .kpi .icon{width:36px;height:36px;font-size:14px}
    .kpi h3{font-size:0.75rem}
    .kpi .v{font-size:1.05rem}
    .header-card{padding:10px}
    .quick-actions{flex-direction:column}
    .quick-btn{width:100%;text-align:left;padding:10px;border-radius:8px}
    .embed{height:360px}
    /* Dataframe and charts: make them take full width and be scrollable */
    .stDataFrame, .stTable, .element-container{width:100% !important}
    .streamlit-expanderHeader{font-size:0.95rem}
}

@media (max-width: 420px) {
    /* Further reduction for small phones */
    .kpi{flex-direction:column;align-items:flex-start}
    .kpi .icon{width:34px;height:34px}
    .kpi .v{font-size:1rem}
    .header-card{padding:8px}
    .small{font-size:0.85rem}
    .embed{height:300px}
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ======================
# Danh mục cận lâm sàng thường dùng
# ======================
COMMON_TESTS: List[Tuple[str, str]] = [
    ("XN máu", "Tổng phân tích tế bào máu"),
    ("XN máu", "Sinh hoá cơ bản"),
    ("XN máu", "Đông máu"),
    ("XN máu", "Đường huyết"),
    ("XN máu", "HbA1c"),
    ("X-quang", "X-quang ngực thẳng"),
    ("Siêu âm", "Siêu âm ổ bụng"),
    ("CT", "CT sọ não không cản quang"),
    ("Khác", "Điện tim"),
]

# ======================
# Helpers DB
# ======================
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
