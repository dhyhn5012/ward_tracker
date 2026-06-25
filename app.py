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
    return conn

def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols

def init_db() -> None:
    os.makedirs(DUTY_DIR, exist_ok=True)
    os.makedirs(PATIENT_UPLOAD_DIR, exist_ok=True)
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medical_id TEXT,
            name TEXT,
            dob TEXT,
            ward TEXT,
            bed TEXT,
            admission_date TEXT,
            discharge_date TEXT,
            severity INTEGER,
            surgery_needed INTEGER,
            planned_treatment_days INTEGER,
            meds TEXT,
            notes TEXT,
            active INTEGER DEFAULT 1,
            diagnosis TEXT,
            operated INTEGER DEFAULT 0
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            order_type TEXT,
            description TEXT,
            date_ordered TEXT,
            scheduled_date TEXT,
            status TEXT,
            result TEXT,
            result_date TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS ward_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            visit_date TEXT,
            general_status TEXT,
            system_exam TEXT,
            plan TEXT,
            extra_tests TEXT,
            extra_tests_note TEXT,
            created_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS duty_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT,           -- 'hospital' | 'department'
            filename TEXT,
            mime TEXT,
            path TEXT,
            uploaded_at TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS patient_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            filename TEXT,
            mime TEXT,
            path TEXT,
            note TEXT,
            uploaded_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )""")
        conn.commit()

        # Migration an toàn (nếu thiếu cột)
        if not _column_exists(conn, "patients", "diagnosis"):
            try:
                conn.execute("ALTER TABLE patients ADD COLUMN diagnosis TEXT"); conn.commit()
            except Exception: pass
        if not _column_exists(conn, "patients", "operated"):
            try:
                conn.execute("ALTER TABLE patients ADD COLUMN operated INTEGER DEFAULT 0"); conn.commit()
            except Exception: pass
        if not _column_exists(conn, "patients", "discharge_time"):
            try:
                conn.execute("ALTER TABLE patients ADD COLUMN discharge_time TEXT"); conn.commit()
            except Exception: pass
        if not _column_exists(conn, "patients", "discharge_prescription"):
            try:
                conn.execute("ALTER TABLE patients ADD COLUMN discharge_prescription TEXT"); conn.commit()
            except Exception: pass
        if not _column_exists(conn, "patients", "discharge_advice"):
            try:
                conn.execute("ALTER TABLE patients ADD COLUMN discharge_advice TEXT"); conn.commit()
            except Exception: pass

        # Clean nhẹ
        try:
            conn.execute("UPDATE patients SET severity=0 WHERE severity IS NULL"); conn.commit()
        except Exception: pass

def _exec(query: str, params: tuple = ()) -> None:
    with get_conn() as conn:
        conn.execute(query, params)
        conn.commit()

def add_patient(patient: Dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO patients
            (medical_id, name, dob, ward, bed, admission_date, severity, surgery_needed,
             planned_treatment_days, meds, notes, active, diagnosis, operated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)
        """, (
            patient.get("medical_id"),
            patient.get("name"),
            patient.get("dob"),
            patient.get("ward"),
            patient.get("bed"),
            patient.get("admission_date"),
            patient.get("severity"),
            1 if patient.get("surgery_needed") else 0,
            patient.get("planned_treatment_days"),
            patient.get("meds"),
            patient.get("notes"),
            patient.get("diagnosis"),
            1 if patient.get("operated") else 0,
        ))
        conn.commit()
        return int(cur.lastrowid)

def update_patient_operated(patient_id: int, operated: bool) -> None:
    _exec("UPDATE patients SET operated=? WHERE id=?", (1 if operated else 0, patient_id))

def add_order(order: Dict[str, Any]) -> None:
    _exec("""
    INSERT INTO orders
    (patient_id, order_type, description, date_ordered, scheduled_date, status)
    VALUES (?,?,?,?,?,?)
    """, (order["patient_id"], order["order_type"], order.get("description", ""),
          order.get("date_ordered"), order.get("scheduled_date"), order.get("status", "pending")))

def add_ward_round(rec: Dict[str, Any]) -> None:
    _exec("""
    INSERT INTO ward_rounds
    (patient_id, visit_date, general_status, system_exam, plan, extra_tests, extra_tests_note, created_at)
    VALUES (?,?,?,?,?,?,?,?)
    """, (
        rec["patient_id"], rec["visit_date"], rec.get("general_status",""),
        rec.get("system_exam",""), rec.get("plan",""),
        rec.get("extra_tests",""), rec.get("extra_tests_note",""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

def mark_order_done(order_id: int, result_text: Optional[str] = None) -> None:
    now = date.today().strftime(DATE_FMT)
    _exec("UPDATE orders SET status='done', result=?, result_date=? WHERE id=?", (result_text, now, order_id))

def discharge_patient(patient_id: int, prescription: str = "", advice: str = "") -> None:
    now_day = date.today().strftime(DATE_FMT)
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _exec("""
        UPDATE patients
        SET discharge_date=?, discharge_time=?, discharge_prescription=?, discharge_advice=?, active=0
        WHERE id=?
    """, (now_day, now_time, prescription.strip(), advice.strip(), patient_id))

def undo_discharge(patient_id: int) -> None:
    _exec("""
        UPDATE patients
        SET discharge_date=NULL, discharge_time=NULL, discharge_prescription=NULL, discharge_advice=NULL, active=1
        WHERE id=?
    """, (patient_id,))

@st.cache_data(ttl=30, show_spinner=False)
def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)

def sanitize_filename(name: str) -> str:
    base = pathlib.Path(name).name
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", base)

def save_patient_file(patient_id: int, uploaded_file, note: str = "") -> None:
    os.makedirs(PATIENT_UPLOAD_DIR, exist_ok=True)
    raw_name = sanitize_filename(uploaded_file.name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_name = f"patient_{patient_id}_{ts}_{raw_name}"
    full_path = os.path.join(PATIENT_UPLOAD_DIR, final_name)
    with open(full_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    mime = uploaded_file.type or mimetypes.guess_type(raw_name)[0] or "application/octet-stream"
    _exec("""
        INSERT INTO patient_files(patient_id, filename, mime, path, note, uploaded_at)
        VALUES (?,?,?,?,?,?)
    """, (patient_id, raw_name, mime, full_path, note.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def reset_clinical_data() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM patient_files")
        conn.execute("DELETE FROM ward_rounds")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM patients")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('patient_files','ward_rounds','orders','patients')")
        conn.commit()

# ======================
# Utilities
# ======================
def days_between(d1: Optional[str], d2: Optional[str] = None) -> Optional[int]:
    if not d1:
        return None
    d1d = datetime.strptime(d1, DATE_FMT).date()
    d2d = datetime.strptime(d2, DATE_FMT).date() if d2 else date.today()
    return (d2d - d1d).days

def calc_age(dob_str: Optional[str]) -> Optional[int]:
    if not dob_str:
        return None
    try:
        d = datetime.strptime(dob_str, DATE_FMT).date()
        today = date.today()
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    except Exception:
        return None

def export_excel(sheets: Dict[str, pd.DataFrame]) -> BytesIO:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:30], index=False)
    buffer.seek(0)
    return buffer

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        st.rerun()

def load_sample_data():
    p1 = {"medical_id":"BN001","name":"Nguyễn A","dob":"1975-02-10","ward":"304","bed":"01",
          "admission_date":date.today().strftime(DATE_FMT),"severity":4,"surgery_needed":1,
          "planned_treatment_days":7,"meds":"Thuốc A","notes":"Theo dõi huyết áp","diagnosis":"Tăng huyết áp","operated":0}
    p2 = {"medical_id":"BN002","name":"Trần B","dob":"1965-06-15","ward":"305","bed":"02",
          "admission_date":(date.today()-timedelta(days=2)).strftime(DATE_FMT),"severity":2,
          "surgery_needed":0,"planned_treatment_days":3,"meds":"Thuốc B","notes":"","diagnosis":"ĐTĐ typ 2","operated":1}
    p3 = {"medical_id":"BN003","name":"Lê C","dob":"1988-11-22","ward":"306","bed":"05",
          "admission_date":(date.today()-timedelta(days=6)).strftime(DATE_FMT),"severity":5,
          "surgery_needed":1,"planned_treatment_days":10,"meds":"Thuốc C","notes":"Theo dõi sau mổ","diagnosis":"Chấn thương sọ não","operated":0}
    id1 = add_patient(p1); id2 = add_patient(p2); id3 = add_patient(p3)

    add_order({"patient_id":id1,"order_type":"CT","description":"CT não",
               "date_ordered":date.today().strftime(DATE_FMT),
               "scheduled_date":(date.today()+timedelta(days=1)).strftime(DATE_FMT),
               "status":"scheduled"})
    add_order({"patient_id":id2,"order_type":"XN máu","description":"Tổng phân tích",
               "date_ordered":(date.today()-timedelta(days=1)).strftime(DATE_FMT),
               "scheduled_date":date.today().strftime(DATE_FMT),
               "status":"pending"})
    add_order({"patient_id":id3,"order_type":"Siêu âm","description":"Ổ bụng",
               "date_ordered":date.today().strftime(DATE_FMT),
               "scheduled_date":(date.today()+timedelta(days=2)).strftime(DATE_FMT),
               "status":"scheduled"})

# ======================
# Điều hướng: PAGES + helper
# ======================
PAGES = [
    "Buổi sáng",
    "Trang chủ",
    "Tổng quan",
    "Nhập viện mới",
    "Đi buồng",
    "Lịch XN/Chụp",
    "Xuất viện",
    "Lịch trực",
    "Tìm kiếm & Lịch sử",
    "Chỉnh sửa BN",
    "Báo cáo",
    "Cài đặt / Demo",
]

def go_edit(pid: int):
    st.session_state.active_page = "Chỉnh sửa BN"
    st.session_state.edit_patient_id = int(pid)
    safe_rerun()

# ======================
# Khởi tạo
# ======================
init_db()

# ======================
# Bảo vệ đơn giản bằng mật khẩu
# ======================
if APP_PASSWORD:
    pw = st.sidebar.text_input("🔐 Mật khẩu ứng dụng", type="password")
    if pw != APP_PASSWORD:
        st.sidebar.warning("Nhập mật khẩu để truy cập ứng dụng")
        st.stop()

# ======================
# Sidebar
# ======================
st.sidebar.title("🩺 Menu")
if "active_page" not in st.session_state:
    st.session_state.active_page = "Buổi sáng"
default_index = PAGES.index(st.session_state.active_page) if st.session_state.active_page in PAGES else 0
selected_page = st.sidebar.radio("Chọn trang", PAGES, index=default_index)
st.session_state.active_page = selected_page
page = selected_page

# ======================
# Helper cho Tổng quan (tuần)
# ======================
def _to_date(s: Optional[str]) -> Optional[date]:
    if not s: return None
    try:
        return datetime.strptime(s, DATE_FMT).date()
    except Exception:
        return None

def week_range(today: date, offset_weeks: int = 0) -> Tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    monday = monday + timedelta(weeks=offset_weeks)
    sunday = monday + timedelta(days=6)
    return monday, sunday

def patients_active_between(dstart: date, dend: date) -> pd.DataFrame:
    df = query_df("SELECT * FROM patients")
    if df.empty: return df
    df = df.copy()
    df["ad"] = df["admission_date"].apply(_to_date)
    df["dd"] = df["discharge_date"].apply(_to_date)
    mask = (df["ad"].notna()) & (df["ad"] <= dend) & ((df["dd"].isna()) | (df["dd"] >= dstart))
    return df[mask]

def count_discharges_between(dstart: date, dend: date) -> int:
    df = query_df("SELECT discharge_date FROM patients WHERE discharge_date IS NOT NULL")
    if df.empty: return 0
    df["dd"] = df["discharge_date"].apply(_to_date)
    return int(((df["dd"] >= dstart) & (df["dd"] <= dend)).sum())

def count_orders_between(dstart: date, dend: date) -> int:
    df = query_df("SELECT scheduled_date FROM orders")
    if df.empty: return 0
    df["sd"] = df["scheduled_date"].apply(_to_date)
    return int(((df["sd"] >= dstart) & (df["sd"] <= dend)).sum())

def avg_days_treated_in_week(dstart: date, dend: date) -> float:
    df = patients_active_between(dstart, dend)
    if df.empty: return 0.0
    def overlap_days(ad: Optional[date], dd: Optional[date]) -> int:
        start = max(ad or dstart, dstart)
        end   = min((dd or dend), dend)
        return max(0, (end - start).days + 1)
    ov = df.apply(lambda r: overlap_days(_to_date(r["admission_date"]), _to_date(r["discharge_date"])), axis=1)
    return round(float(ov.mean()), 1)

# ======================
# Các helper cho "Đi buồng": truy vấn phương án điều trị mới nhất
# ======================
def latest_plan_map_all_patients() -> Dict[int, str]:
    """
    Lấy phương án điều trị mới nhất (bất kể ngày) cho mỗi BN.
    Trả về dict: patient_id -> plan (string)
    """
    df = query_df("""
        SELECT w.patient_id, w.plan
        FROM ward_rounds w
        JOIN (
            SELECT patient_id, MAX(id) AS max_id
            FROM ward_rounds
            GROUP BY patient_id
        ) t ON w.id = t.max_id
    """)
    if df.empty: return {}
    out = {}
    for r in df.to_dict(orient="records"):
        out[int(r["patient_id"])] = r.get("plan") or ""
    return out

def rounds_latest_today_with_plan() -> pd.DataFrame:
    """
    Lấy lần khám mới nhất trong NGÀY HÔM NAY cho mỗi BN (active),
    kèm thông tin BN và cột w.plan (Phương án điều trị tiếp).
    """
    today_str = date.today().strftime(DATE_FMT)
    df = query_df("""
        SELECT p.id as pid, p.name, p.dob, p.diagnosis, p.notes, p.ward, w.plan
        FROM ward_rounds w
        JOIN (
            SELECT patient_id, MAX(id) AS max_id
            FROM ward_rounds
            WHERE visit_date = ?
            GROUP BY patient_id
        ) t ON w.id = t.max_id
        JOIN patients p ON p.id = w.patient_id
        WHERE p.active = 1
        ORDER BY p.name
    """, (today_str,))
    return df

def get_patient_info(pid: int) -> Optional[Dict[str, Any]]:
    df = query_df("SELECT * FROM patients WHERE id=?", (pid,))
    if df.empty: return None
    return df.iloc[0].to_dict()

# ======================
# Dashboard helpers (Trang chủ)
# ======================
def dashboard_stats(filters: Dict[str, Any]) -> Dict[str, Any]:
    base_active = "SELECT * FROM patients WHERE active=1"
    params = []
    if filters.get("ward") and filters["ward"] != "Tất cả":
        base_active += " AND ward=?"; params.append(filters["ward"])
    df_active = query_df(base_active, tuple(params))
    plan_map = latest_plan_map_all_patients()
    total_active = len(df_active)
    patients_per_ward = (
        df_active.groupby("ward").size().reset_index(name="Số BN").sort_values("Số BN", ascending=False)
        if total_active > 0 else pd.DataFrame(columns=["ward","Số BN"])
    )
    if total_active > 0:
        df_active = df_active.copy()
        df_active["days_in_hospital"] = df_active["admission_date"].apply(lambda d: days_between(d))
        df_active["plan_next"] = df_active["id"].map(plan_map).fillna("")
        avg_days = round(df_active["days_in_hospital"].mean(), 1)
    else:
        avg_days = 0
    df_orders = query_df("""
        SELECT o.*, p.name, p.ward FROM orders o
        LEFT JOIN patients p ON o.patient_id=p.id
    """)
    pending_patients = df_orders[df_orders["status"]!="done"]["patient_id"].nunique() if not df_orders.empty else 0
    today_str = date.today().strftime(DATE_FMT)
    scheduled_not_done = 0
    if not df_orders.empty:
        mask = (df_orders["status"]!="done") & (df_orders["scheduled_date"].notna()) & (df_orders["scheduled_date"]<=today_str)
        scheduled_not_done = int(mask.sum())
    return {
        "total_active": total_active,
        "patients_per_ward": patients_per_ward,
        "avg_days": avg_days,
        "count_wait_surg": int(query_df("SELECT COUNT(*) as c FROM patients WHERE active=1 AND surgery_needed=1")["c"][0]) if total_active>=0 else 0,
        "pending_patients": pending_patients,
        "scheduled_not_done": scheduled_not_done,
        "df_active": df_active,
        "df_orders": df_orders,
        "plan_map": plan_map,
    }

def kpi(title: str, value: Any, icon: Optional[str] = None):
    icon_html = f"<div class='icon'>{icon}</div>" if icon else ""
    st.markdown(f"""
        <div class='kpi'>
            {icon_html}
            <div>
                <h3>{title}</h3>
                <div class='v'>{value}</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

def ward_pie_chart(df: pd.DataFrame):
    if df.empty:
        st.info("Chưa có dữ liệu BN theo phòng."); return
    chart = (
        alt.Chart(df.rename(columns={"ward":"Phòng"}))
        .mark_arc()
        .encode(theta="Số BN:Q", color="Phòng:N",
                tooltip=["Phòng:N","Số BN:Q"])
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)

def orders_status_pie_chart(df_orders: pd.DataFrame):
    if df_orders.empty:
        st.info("Chưa có dữ liệu chỉ định."); return
    stat = df_orders.groupby("status").size().reset_index(name="Số lượng")
    stat.rename(columns={"status":"Trạng thái"}, inplace=True)
    chart = (
        alt.Chart(stat)
        .mark_arc()
        .encode(theta="Số lượng:Q", color="Trạng thái:N",
                tooltip=["Trạng thái:N","Số lượng:Q"])
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)

QUICK_STATUS = ["Ổn", "Theo dõi sát", "Đau", "Sốt", "Nặng hơn", "Có biến cố"]
QUICK_NEURO = ["Không đổi", "Tốt hơn", "Xấu hơn", "Không ghi nhận thiếu sót mới", "Yếu/liệt mới", "Đau lan/tê bì"]
QUICK_WOUND = ["Không có vết mổ", "Vết mổ khô", "Thấm ít dịch", "Đỏ/nề", "Nghi nhiễm trùng", "Tụ dịch/chảy dịch"]
QUICK_DRAIN = ["Không dẫn lưu", "<50 ml/24h", "50-100 ml/24h", ">100 ml/24h", "Dịch máu", "Dịch trong"]
QUICK_DECISIONS = ["Nằm tiếp", "Có thể ra viện", "Chuẩn bị mổ", "Sau mổ theo dõi", "Cần CLS", "Cần hội chẩn", "Chuyển ICU"]
QUICK_TASKS = ["Thay băng", "Rút dẫn lưu", "Tập PHCN", "Theo dõi sốt", "Theo dõi đau", "Kê đơn ra viện", "Hẹn tái khám", "Báo mổ", "Hội chẩn"]

def priority_label(row: Dict[str, Any], done_today: bool) -> str:
    if not done_today:
        return "Chưa khám"
    if row.get("surgery_needed") == 1 and row.get("operated") != 1:
        return "Chờ mổ"
    if row.get("operated") == 1:
        return "Sau mổ"
    if row.get("over_planned"):
        return "Nằm lâu"
    return "Theo dõi"

def build_quick_round_text(
    patient: Dict[str, Any],
    status: str,
    pain_score: int,
    neuro: str,
    wound: str,
    drain: str,
    decision: str,
    tasks: List[str],
    note: str,
) -> Tuple[str, str, str]:
    pain_level = "không đau hoặc đau nhẹ" if pain_score <= 3 else ("đau mức vừa" if pain_score <= 6 else "đau nhiều")
    general_status = f"BN {status.lower()}, {pain_level} (VAS {pain_score}/10)."
    system_exam = f"Thần kinh: {neuro.lower()}. Vết mổ: {wound.lower()}. Dẫn lưu: {drain.lower()}."
    task_text = ", ".join(tasks) if tasks else "theo dõi và điều trị theo y lệnh"
    plan = f"Hôm nay: {decision}. Việc cần làm: {task_text}."
    if note.strip():
        plan += f" Ghi chú: {note.strip()}"
    return general_status, system_exam, plan

def quick_order_suggestions(decision: str, tasks: List[str]) -> List[str]:
    suggestions = []
    if decision == "Cần CLS":
        suggestions.extend(["XN máu — Tổng phân tích tế bào máu", "XN máu — Sinh hoá cơ bản"])
    if "Báo mổ" in tasks:
        suggestions.append("XN máu — Đông máu")
    if decision == "Sau mổ theo dõi":
        suggestions.append("XN máu — Tổng phân tích tế bào máu")
    return suggestions

# ======================
# Buổi sáng trước đi buồng
# ======================
if page == "Buổi sáng":
    st.title("🌅 Buổi sáng trước đi buồng")
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime(DATE_FMT)
    yday_str = yesterday.strftime(DATE_FMT)

    df_wards = query_df("SELECT DISTINCT ward FROM patients WHERE active=1 AND ward IS NOT NULL AND ward<>'' ORDER BY ward")
    ward_options = ["Tất cả"] + (df_wards["ward"].tolist() if not df_wards.empty else [])
    selected_ward = st.selectbox("Phạm vi xem", ward_options, index=0, key="morning_ward")

    active_sql = "SELECT * FROM patients WHERE active=1"
    active_params: List[Any] = []
    if selected_ward != "Tất cả":
        active_sql += " AND ward=?"
        active_params.append(selected_ward)
    active_sql += " ORDER BY ward, bed, name"
    df_active = query_df(active_sql, tuple(active_params))

    if df_active.empty:
        st.info("Hiện không có bệnh nhân đang nằm trong phạm vi đã chọn.")
        st.stop()

    df_active = df_active.copy()
    df_active["days_in_hospital"] = df_active["admission_date"].apply(lambda d: days_between(d) or 0)
    df_active["diagnosis_group"] = df_active["diagnosis"].fillna("").apply(lambda x: x.strip() or "Chưa ghi chẩn đoán")
    df_active["over_planned"] = df_active.apply(
        lambda r: bool(r.get("planned_treatment_days") and r["days_in_hospital"] > int(r.get("planned_treatment_days") or 0)),
        axis=1
    )

    total_active = len(df_active)
    operated_count = int((df_active["operated"] == 1).sum()) if "operated" in df_active else 0
    waiting_surgery = int(((df_active["surgery_needed"] == 1) & (df_active["operated"] != 1)).sum())
    avg_los = round(float(df_active["days_in_hospital"].mean()), 1) if total_active else 0
    over_planned_rate = round(float(df_active["over_planned"].mean() * 100), 1) if total_active else 0
    longest = df_active.sort_values("days_in_hospital", ascending=False).iloc[0].to_dict()

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: kpi("BN đang nằm", total_active, icon="BN")
    with c2: kpi("BN đã mổ", operated_count, icon="PT")
    with c3: kpi("BN chờ mổ", waiting_surgery, icon="M")
    with c4: kpi("Ngày điều trị TB", avg_los, icon="D")
    with c5: kpi("Quá ngày dự kiến", f"{over_planned_rate}%", icon="%")

    st.markdown(
        f"<div class='header-card'><b>BN nằm lâu nhất:</b> {longest.get('name','—')} "
        f"<span class='patient-chip'>P.{longest.get('ward','—')} / G.{longest.get('bed','—')}</span> "
        f"<span class='patient-chip'>{int(longest.get('days_in_hospital') or 0)} ngày</span> "
        f"<span class='muted-line'>{longest.get('diagnosis') or ''}</span></div>",
        unsafe_allow_html=True
    )

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Tỷ lệ mặt bệnh")
        disease_df = (
            df_active.groupby("diagnosis_group")
            .size()
            .reset_index(name="Số BN")
            .sort_values("Số BN", ascending=False)
        )
        disease_df["Tỷ lệ %"] = (disease_df["Số BN"] / total_active * 100).round(1)
        st.altair_chart(
            alt.Chart(disease_df.head(12))
            .mark_bar()
            .encode(
                x=alt.X("Số BN:Q", title="Số bệnh nhân"),
                y=alt.Y("diagnosis_group:N", sort="-x", title="Chẩn đoán"),
                tooltip=["diagnosis_group", "Số BN", "Tỷ lệ %"],
            )
            .properties(height=320),
            use_container_width=True,
        )
        st.dataframe(
            disease_df.rename(columns={"diagnosis_group": "Mặt bệnh"}),
            use_container_width=True,
            hide_index=True,
        )

    with right:
        st.subheader("Việc cần xem sáng nay")
        rounds_today = query_df("SELECT DISTINCT patient_id FROM ward_rounds WHERE visit_date=?", (today_str,))
        done_ids = set(rounds_today["patient_id"].astype(int).tolist()) if not rounds_today.empty else set()
        df_need_round = df_active[~df_active["id"].astype(int).isin(done_ids)].copy()
        st.write(f"BN chưa có khám hôm nay: **{len(df_need_round)}**")
        show_cols = ["medical_id", "name", "ward", "bed", "diagnosis", "days_in_hospital", "surgery_needed", "operated"]
        st.dataframe(
            df_need_round[show_cols].rename(columns={
                "medical_id": "Mã BA", "name": "Họ tên", "ward": "Phòng", "bed": "Giường",
                "diagnosis": "Chẩn đoán", "days_in_hospital": "Ngày ĐT",
                "surgery_needed": "Cần mổ", "operated": "Đã mổ"
            }),
            use_container_width=True,
            hide_index=True,
        )

        y_orders = query_df("""
            SELECT o.*, p.name, p.medical_id, p.ward, p.bed, p.diagnosis
            FROM orders o
            JOIN patients p ON p.id=o.patient_id
            WHERE o.date_ordered=? AND (?='Tất cả' OR p.ward=?)
            ORDER BY p.ward, p.bed, p.name
        """, (yday_str, selected_ward, selected_ward))
        st.write(f"CLS đã yêu cầu hôm qua ({yesterday.strftime('%d/%m/%Y')}): **{len(y_orders)}**")
        if y_orders.empty:
            st.caption("Không có CLS nào được ghi nhận là yêu cầu hôm qua.")
        else:
            st.dataframe(
                y_orders[["medical_id", "name", "ward", "bed", "order_type", "description", "scheduled_date", "status"]].rename(columns={
                    "medical_id": "Mã BA", "name": "Họ tên", "ward": "Phòng", "bed": "Giường",
                    "order_type": "Loại", "description": "Nội dung", "scheduled_date": "Ngày làm",
                    "status": "Trạng thái"
                }),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")
    st.subheader("Đi buồng nhanh")
    quick_df = df_active.copy()
    quick_df["Đã khám hôm nay"] = quick_df["id"].astype(int).isin(done_ids)
    quick_df["Nhóm"] = quick_df.apply(lambda r: priority_label(r.to_dict(), bool(r["Đã khám hôm nay"])), axis=1)
    quick_df["Một dòng"] = quick_df.apply(
        lambda r: f"{r.get('ward') or '—'}/{r.get('bed') or '—'} - {r.get('name') or ''} - {r.get('diagnosis') or 'Chưa ghi chẩn đoán'}",
        axis=1
    )

    group_order = ["Chưa khám", "Chờ mổ", "Sau mổ", "Nằm lâu", "Theo dõi"]
    st.dataframe(
        quick_df[["Nhóm", "Một dòng", "days_in_hospital", "surgery_needed", "operated"]]
        .rename(columns={
            "days_in_hospital": "Ngày ĐT",
            "surgery_needed": "Cần mổ",
            "operated": "Đã mổ",
        })
        .sort_values("Nhóm", key=lambda s: s.map({v: i for i, v in enumerate(group_order)}).fillna(99)),
        use_container_width=True,
        hide_index=True,
    )

    quick_options = quick_df["id"].astype(int).tolist()
    quick_default = quick_options[0]
    if not df_need_round.empty:
        quick_default = int(df_need_round.iloc[0]["id"])
    if "quick_patient_id" in st.session_state and int(st.session_state.quick_patient_id) in quick_options:
        quick_default = int(st.session_state.quick_patient_id)

    quick_pid = st.selectbox(
        "Chọn BN để ghi nhanh",
        options=quick_options,
        index=quick_options.index(quick_default),
        format_func=lambda x: quick_df[quick_df["id"] == x]["Một dòng"].values[0],
        key="quick_patient_id",
    )
    quick_patient = get_patient_info(int(quick_pid))
    quick_latest_plan = latest_plan_map_all_patients().get(int(quick_pid), "")
    st.caption(f"Kế hoạch gần nhất: {quick_latest_plan or 'Chưa có'}")

    with st.form(f"quick_round_form_{quick_pid}", clear_on_submit=True):
        q1, q2, q3 = st.columns([1, 1, 1])
        with q1:
            status = st.selectbox("Diễn biến", QUICK_STATUS, index=0)
            pain_score = st.slider("Đau VAS", min_value=0, max_value=10, value=2)
            operated_now = st.checkbox("Đã phẫu thuật", value=bool(quick_patient.get("operated", 0) if quick_patient else 0))
        with q2:
            neuro = st.selectbox("Thần kinh", QUICK_NEURO, index=0)
            wound = st.selectbox("Vết mổ", QUICK_WOUND, index=1 if quick_patient and quick_patient.get("operated", 0) else 0)
            drain = st.selectbox("Dẫn lưu", QUICK_DRAIN, index=0)
        with q3:
            decision = st.selectbox("Quyết định hôm nay", QUICK_DECISIONS, index=0)
            tasks = st.multiselect("Việc cần làm", QUICK_TASKS)
            note = st.text_area("Ghi chú đặc biệt", height=80, placeholder="Chỉ nhập nếu có điểm khác thường")

        suggested = quick_order_suggestions(decision, tasks)
        all_test_opts = [f"{t[0]} — {t[1]}" for t in COMMON_TESTS]
        default_tests = [x for x in suggested if x in all_test_opts]
        quick_tests = st.multiselect("CLS thêm hôm nay", all_test_opts, default=default_tests)
        try:
            quick_test_date = st.date_input("Ngày làm CLS", value=today, format="DD/MM/YYYY", key=f"quick_cls_date_{quick_pid}")
        except TypeError:
            quick_test_date = st.date_input("Ngày làm CLS", value=today, key=f"quick_cls_date_{quick_pid}")

        general_status, system_exam, plan = build_quick_round_text(
            quick_patient or {}, status, int(pain_score), neuro, wound, drain, decision, tasks, note
        )
        with st.expander("Xem câu khám sẽ lưu", expanded=False):
            st.write("**Toàn thân:**", general_status)
            st.write("**Khám:**", system_exam)
            st.write("**Kế hoạch:**", plan)

        save_quick = st.form_submit_button("Lưu nhanh và chuyển BN tiếp theo")

    if save_quick and quick_patient:
        update_patient_operated(int(quick_pid), operated_now)
        add_ward_round({
            "patient_id": int(quick_pid),
            "visit_date": today_str,
            "general_status": general_status,
            "system_exam": system_exam,
            "plan": plan,
            "extra_tests": ", ".join(quick_tests) if quick_tests else "",
            "extra_tests_note": note.strip(),
        })
        if quick_tests:
            text_to_tuple = {f"{t[0]} — {t[1]}": t for t in COMMON_TESTS}
            for sel in quick_tests:
                ot, desc = text_to_tuple[sel]
                add_order({
                    "patient_id": int(quick_pid),
                    "order_type": ot,
                    "description": desc if not note.strip() else f"{desc} — {note.strip()}",
                    "date_ordered": today_str,
                    "scheduled_date": quick_test_date.strftime(DATE_FMT),
                    "status": "scheduled",
                })

        remaining_ids = [int(x) for x in df_need_round["id"].tolist() if int(x) != int(quick_pid)]
        if remaining_ids:
            st.session_state.quick_patient_id = remaining_ids[0]
            st.session_state.morning_focus_patient = remaining_ids[0]
        st.success("Đã lưu khám nhanh.")
        st.cache_data.clear()
        safe_rerun()

    st.markdown("---")
    st.subheader("Mở từng bệnh nhân")
    patient_options = df_active["id"].astype(int).tolist()
    default_patient = patient_options[0]
    if "morning_focus_patient" in st.session_state and int(st.session_state.morning_focus_patient) in patient_options:
        default_patient = int(st.session_state.morning_focus_patient)
    selected_patient = st.selectbox(
        "Chọn bệnh nhân để xem lại và xử trí",
        options=patient_options,
        index=patient_options.index(default_patient),
        format_func=lambda x: (
            f"{df_active[df_active['id']==x]['medical_id'].values[0] or '—'} - "
            f"{df_active[df_active['id']==x]['name'].values[0]} "
            f"(P.{df_active[df_active['id']==x]['ward'].values[0] or '—'} / G.{df_active[df_active['id']==x]['bed'].values[0] or '—'})"
        ),
        key="morning_focus_patient",
    )

    p = get_patient_info(int(selected_patient))
    if not p:
        st.error("Không tìm thấy bệnh nhân.")
        st.stop()

    st.markdown(
        f"**{p.get('name','')}** — {p.get('medical_id') or '—'}  "
        f"<span class='patient-chip'>P.{p.get('ward') or '—'} / G.{p.get('bed') or '—'}</span> "
        f"<span class='patient-chip'>{days_between(p.get('admission_date')) or 0} ngày điều trị</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"Chẩn đoán: {p.get('diagnosis') or '—'} | Thuốc chính: {p.get('meds') or '—'} | Ghi chú: {p.get('notes') or '—'}")

    t1, t2, t3, t4 = st.tabs(["Khám hôm qua", "CLS hôm qua", "Ảnh/tệp", "Khám hôm nay & ra viện"])

    with t1:
        y_rounds = query_df("""
            SELECT * FROM ward_rounds
            WHERE patient_id=? AND visit_date=?
            ORDER BY id DESC
        """, (int(selected_patient), yday_str))
        if y_rounds.empty:
            st.info("Chưa có dữ liệu khám hôm qua cho bệnh nhân này.")
        else:
            for _, rr in y_rounds.iterrows():
                st.markdown(f"**Lần ghi #{rr['id']} — {rr['created_at']}**")
                st.write("**Toàn thân:**", rr["general_status"] or "—")
                st.write("**Khám bộ phận:**", rr["system_exam"] or "—")
                st.write("**Kế hoạch:**", rr["plan"] or "—")
                if rr["extra_tests"]:
                    st.write("**CLS thêm:**", rr["extra_tests"])
                if rr["extra_tests_note"]:
                    st.write("**Ghi chú CLS:**", rr["extra_tests_note"])
                st.markdown("---")

    with t2:
        p_y_orders = query_df("""
            SELECT * FROM orders
            WHERE patient_id=? AND date_ordered=?
            ORDER BY scheduled_date, id
        """, (int(selected_patient), yday_str))
        if p_y_orders.empty:
            st.info("Bệnh nhân này không có CLS được yêu cầu hôm qua.")
        else:
            for od in p_y_orders.to_dict("records"):
                st.markdown(f"**{od['order_type']}** — {od.get('description') or '—'}")
                st.caption(f"Ngày làm: {od.get('scheduled_date') or '—'} | Trạng thái: {od.get('status') or '—'} | Kết quả: {od.get('result') or '—'}")
                res_key = f"morning_res_{od['id']}"
                result_text = st.text_input("Nhập/cập nhật kết quả", value=od.get("result") or "", key=res_key)
                if st.button("Đánh dấu đã có kết quả", key=f"morning_done_{od['id']}"):
                    mark_order_done(int(od["id"]), result_text)
                    st.success("Đã cập nhật kết quả CLS.")
                    st.cache_data.clear()
                    safe_rerun()

    with t3:
        st.write("Tải ảnh chụp phim, xét nghiệm, đơn thuốc cũ hoặc tệp liên quan để xem lại nhanh khi đi buồng.")
        up_file = st.file_uploader("Thêm ảnh/tệp cho bệnh nhân này", type=None, key=f"patient_file_{selected_patient}")
        file_note = st.text_input("Ghi chú cho tệp", key=f"patient_file_note_{selected_patient}")
        if up_file is not None and st.button("Lưu tệp vào hồ sơ BN", key=f"save_patient_file_{selected_patient}"):
            save_patient_file(int(selected_patient), up_file, file_note)
            st.success("Đã lưu tệp.")
            st.cache_data.clear()
            safe_rerun()

        files = query_df("SELECT * FROM patient_files WHERE patient_id=? ORDER BY uploaded_at DESC", (int(selected_patient),))
        if files.empty:
            st.info("Chưa có ảnh/tệp đã add cho bệnh nhân này.")
        else:
            for f in files.to_dict("records"):
                with st.expander(f"{f['filename']} — {f['uploaded_at']}", expanded=False):
                    st.caption(f.get("note") or "")
                    if os.path.exists(f["path"]):
                        mime = f.get("mime") or ""
                        if mime.startswith("image/"):
                            st.image(f["path"], use_container_width=True)
                        elif mime in ("application/pdf", "application/x-pdf"):
                            with open(f["path"], "rb") as fh:
                                b64 = base64.b64encode(fh.read()).decode("utf-8")
                            st.markdown(
                                f"<div class='embed'><embed src='data:application/pdf;base64,{b64}' type='application/pdf' width='100%' height='100%'/></div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.info("Tệp đã lưu, định dạng này không xem trực tiếp trong app.")
                        with open(f["path"], "rb") as fh:
                            st.download_button("Tải tệp", data=fh.read(), file_name=f["filename"], key=f"dl_patient_file_{f['id']}")
                    else:
                        st.error("Không tìm thấy tệp trên máy chủ.")

    with t4:
        col_round, col_discharge = st.columns([1, 1])
        with col_round:
            st.markdown("#### Khám hôm nay / nằm tiếp")
            with st.form(f"morning_round_form_{selected_patient}", clear_on_submit=True):
                general_status = st.text_area("Tình trạng toàn thân hôm nay", height=90)
                system_exam = st.text_area("Khám bộ phận hôm nay", height=110)
                plan = st.text_area("Nhận định / xử trí / theo dõi tiếp", height=110)
                extra_opts = [f"{t[0]} — {t[1]}" for t in COMMON_TESTS]
                extra_selected = st.multiselect("Yêu cầu CLS hôm nay nếu cần", extra_opts)
                extra_note = st.text_area("Ghi chú/lý do CLS", height=70)
                try:
                    extra_scheduled = st.date_input("Ngày dự kiến thực hiện CLS", value=today, format="DD/MM/YYYY", key=f"morning_cls_date_{selected_patient}")
                except TypeError:
                    extra_scheduled = st.date_input("Ngày dự kiến thực hiện CLS", value=today, key=f"morning_cls_date_{selected_patient}")
                operated_now = st.checkbox("Đã phẫu thuật", value=bool(p.get("operated", 0)))
                save_round = st.form_submit_button("Lưu khám hôm nay")

            if save_round:
                update_patient_operated(int(selected_patient), operated_now)
                add_ward_round({
                    "patient_id": int(selected_patient),
                    "visit_date": today_str,
                    "general_status": general_status.strip(),
                    "system_exam": system_exam.strip(),
                    "plan": plan.strip(),
                    "extra_tests": ", ".join(extra_selected) if extra_selected else "",
                    "extra_tests_note": extra_note.strip(),
                })
                if extra_selected:
                    text_to_tuple = {f"{t[0]} — {t[1]}": t for t in COMMON_TESTS}
                    for sel in extra_selected:
                        ot, desc = text_to_tuple[sel]
                        desc_full = desc if not extra_note.strip() else f"{desc} — {extra_note.strip()}"
                        add_order({
                            "patient_id": int(selected_patient),
                            "order_type": ot,
                            "description": desc_full,
                            "date_ordered": today_str,
                            "scheduled_date": extra_scheduled.strftime(DATE_FMT),
                            "status": "scheduled",
                        })
                st.success("Đã lưu khám hôm nay.")
                st.cache_data.clear()
                safe_rerun()

        with col_discharge:
            st.markdown("#### Ra viện hôm nay")
            with st.form(f"morning_discharge_form_{selected_patient}", clear_on_submit=True):
                prescription = st.text_area("Đơn thuốc khi ra viện", height=130, placeholder="VD: thuốc, liều, số ngày...")
                advice = st.text_area("Tư vấn / hẹn tái khám", height=130, placeholder="VD: dấu hiệu cần quay lại, lịch hẹn, chăm sóc vết mổ...")
                confirm = st.checkbox("Xác nhận cho ra viện hôm nay")
                do_discharge = st.form_submit_button("Ấn ra viện")

            if do_discharge:
                if not confirm:
                    st.warning("Cần tick xác nhận trước khi ra viện.")
                else:
                    discharge_patient(int(selected_patient), prescription, advice)
                    st.success("Đã ra viện và chuyển vào danh sách ra viện hôm nay.")
                    st.session_state.active_page = "Xuất viện"
                    st.session_state.discharge_view_date = today
                    st.cache_data.clear()
                    safe_rerun()

# ======================
# Trang chủ
# ======================
elif page == "Trang chủ":
    st.title("📊 Dashboard — Theo dõi bệnh nhân")

    # Hiển thị banner (nếu đã tải lên trong thư mục static/)
    banner_paths = ["static/banner.png", "static/banner.jpg", "static/banner.jpeg", "static/banner.gif"]
    banner_file = next((p for p in banner_paths if os.path.exists(p)), None)
    if banner_file:
        try:
            st.image(banner_file, use_container_width=True)
        except Exception:
            st.markdown(f"![banner]({banner_file})")
    else:
        st.markdown(
            "<div class='header-card'>"
            "<h2 style='margin:0'>Phần mềm theo dõi bệnh nhân</h2>"
            "<p class='small' style='margin:6px 0 0 0'>Không có banner. Vào Cài đặt / Demo để tải ảnh hiển thị ở đầu trang.</p>"
            "</div>",
            unsafe_allow_html=True
        )

    # Quick action toolbar
    st.markdown("<div class='header-card'>", unsafe_allow_html=True)
    col_a, col_b, col_c, col_d, col_e = st.columns([2,2,2,2,6])
    with col_a:
        if st.button("➕ Thêm BN"):
            st.session_state.active_page = "Nhập viện mới"; safe_rerun()
    with col_b:
        if st.button("🚶 Đi buồng"):
            st.session_state.active_page = "Đi buồng"; safe_rerun()
    with col_c:
        if st.button("🧪 Lịch XN"):
            st.session_state.active_page = "Lịch XN/Chụp"; safe_rerun()
    with col_d:
        if st.button("📑 Báo cáo"):
            st.session_state.active_page = "Báo cáo"; safe_rerun()
    with col_e:
        st.markdown("<div class='small'>Ngắn gọn: Tạo BN mới, mở form đi buồng, quản lý chỉ định, xem báo cáo nhanh.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    df_all_wards = query_df("SELECT DISTINCT ward FROM patients WHERE ward IS NOT NULL AND ward<>'' ORDER BY ward")
    ward_list = ["Tất cả"] + (df_all_wards["ward"].tolist() if not df_all_wards.empty else [])
    f_col1, f_col2 = st.columns([1,2])
    with f_col1: ward_filter = st.selectbox("Lọc theo phòng", ward_list, index=0)
    with f_col2: st.markdown("<div class='small'>Gợi ý: dùng bộ lọc để xem nhanh khoa/phòng.</div>", unsafe_allow_html=True)

    stats = dashboard_stats({"ward": ward_filter})

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: kpi("BN đang điều trị", stats["total_active"], icon="🫀")
    with c2: kpi("Thời gian điều trị TB (ngày)", stats["avg_days"], icon="⏱️")
    with c3: kpi("Chờ mổ", stats["count_wait_surg"], icon="🔪")
    with c4: kpi("BN có order chưa xong", stats["pending_patients"], icon="📋")
    with c5: kpi("Order quá hạn / đến hạn", stats["scheduled_not_done"], icon="⚠️")
    st.markdown("---")

    st.subheader("BN theo phòng")
    ward_pie_chart(stats["patients_per_ward"])

    st.subheader("Trạng thái chỉ định")
    orders_status_pie_chart(stats["df_orders"])

    with st.expander("📋 Danh sách BN (đang điều trị)", expanded=True):
        df_active = stats["df_active"]
        if df_active.empty:
            st.info("Không có bệnh nhân đang nằm.")
        else:
            base_cols = [
                "id",
                "medical_id",
                "name",
                "ward",
                "bed",
                "surgery_needed",
                "admission_date",
                "diagnosis",
                "plan_next",
                "notes",
                "operated",
            ]
            view_cols = [c for c in base_cols if c in df_active.columns]
            st.dataframe(
                df_active[view_cols].rename(
                    columns={
                        "medical_id": "Mã BA",
                        "name": "Họ tên",
                        "ward": "Phòng",
                        "bed": "Giường",
                        "surgery_needed": "Cần mổ",
                        "admission_date": "Ngày NV",
                        "diagnosis": "Chẩn đoán",
                        "plan_next": "PA điều trị tiếp",
                        "notes": "Ghi chú",
                        "operated": "Đã phẫu thuật",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
            for row in df_active.to_dict(orient="records"):
                cols = st.columns([1,3,1,1,1,1,1])
                cols[0].markdown(f"**{row['medical_id']}**")
                diag_txt = f"<br/><span class='small'>Chẩn đoán: {row.get('diagnosis','')}</span>" if row.get("diagnosis") else ""
                cols[1].markdown(f"**{row['name']}**  \n<span class='small'>{row.get('notes','')}</span>{diag_txt}", unsafe_allow_html=True)
                cols[2].markdown(f"{row.get('ward','')}/{row.get('bed','') or ''}")
                cols[3].markdown("🔪 Cần mổ" if row.get("surgery_needed")==1 else "")
                cols[4].markdown("✅" if row.get("operated")==1 else "✗")
                if cols[5].button("✏️ Chỉnh sửa", key=f"edit_home_{row['id']}"):
                    go_edit(row["id"])
                if cols[6].button("Xuất viện", key=f"dis_{row['id']}"):
                    discharge_patient(row["id"]); st.success(f"Đã xuất viện {row['name']}"); safe_rerun()

# ======================
# Trang TỔNG QUAN
# ======================
elif page == "Tổng quan":
    st.title("📈 Tổng quan theo tuần")

    # Tính toán số liệu
    today = date.today()
    this_start, this_end = week_range(today, 0)
    last_start, last_end = week_range(today, -1)

    try:
        active_this_df = patients_active_between(this_start, this_end)
        treatment_this = len(active_this_df)
        discharge_this = count_discharges_between(this_start, this_end)
        orders_this    = count_orders_between(this_start, this_end)
        avg_days_this  = avg_days_treated_in_week(this_start, this_end)

        active_last_df = patients_active_between(last_start, last_end)
        treatment_last = len(active_last_df)
        discharge_last = count_discharges_between(last_start, last_end)
        orders_last    = count_orders_between(last_start, last_end)
        avg_days_last  = avg_days_treated_in_week(last_start, last_end)
    except Exception as e:
        import traceback
        st.error("Lỗi khi tính toán số liệu. Xem chi tiết")
        st.code(traceback.format_exc())
        st.stop()

    # Debug expander (ẩn) để kiểm tra nội dung trung gian
    with st.expander("Debug số liệu (chỉ hiện khi cần)"):
        st.write("this_start", this_start, "this_end", this_end)
        st.write("treatment_this", treatment_this, type(treatment_this))
        st.write("discharge_this", discharge_this, type(discharge_this))
        st.write("orders_this", orders_this, type(orders_this))
        st.write("avg_days_this", avg_days_this, type(avg_days_this))
        st.write("treatment_last", treatment_last)
        st.write("discharge_last", discharge_last)
        st.write("orders_last", orders_last)
        st.write("avg_days_last", avg_days_last)
        st.markdown("---")
        st.write("Sample active_this_df:")
        try:
            st.dataframe(active_this_df.head(), use_container_width=True)
        except Exception as e:
            st.write("Không thể hiển thị dataframe:", e)

    # Vẽ biểu đồ — mỗi phần có try/except riêng để bắt lỗi
    st.subheader("Ra viện vs Lượt điều trị (tuần này)")
    try:
        df1 = pd.DataFrame({"Chỉ số": ["Lượt điều trị", "Ra viện"], "Giá trị": [treatment_this, discharge_this]})
        st.altair_chart(alt.Chart(df1).mark_bar().encode(x="Chỉ số:N", y="Giá trị:Q", tooltip=["Chỉ số","Giá trị"]).properties(height=280), use_container_width=True)
    except Exception:
        import traceback
        st.error("Lỗi khi vẽ biểu đồ Ra viện vs Lượt điều trị")
        st.code(traceback.format_exc())

    st.subheader("Chỉ định cận lâm sàng (tuần này) so với Lượt điều trị")
    try:
        df2 = pd.DataFrame({"Hạng mục": ["Chỉ định CLS", "Lượt điều trị"], "Số lượng": [orders_this, treatment_this]})
        st.altair_chart(alt.Chart(df2).mark_bar().encode(x="Hạng mục:N", y="Số lượng:Q", tooltip=["Hạng mục","Số lượng"]).properties(height=280), use_container_width=True)
    except Exception:
        import traceback
        st.error("Lỗi khi vẽ biểu đồ Chỉ định CLS")
        st.code(traceback.format_exc())

    st.subheader("So sánh tuần này và tuần trước")
    try:
        comp_df = pd.DataFrame([
            {"Chỉ số":"Số ngày điều trị TB/BN", "Tuần":"Tuần trước", "Giá trị": avg_days_last},
            {"Chỉ số":"Số ngày điều trị TB/BN", "Tuần":"Tuần này",   "Giá trị": avg_days_this},
            {"Chỉ số":"Ra viện",                "Tuần":"Tuần trước", "Giá trị": discharge_last},
            {"Chỉ số":"Ra viện",                "Tuần":"Tuần này",   "Giá trị": discharge_this},
            {"Chỉ số":"Lượt điều trị",          "Tuần":"Tuần trước", "Giá trị": treatment_last},
            {"Chỉ số":"Lượt điều trị",          "Tuần":"Tuần này",   "Giá trị": treatment_this},
        ])
        chart3 = (
            alt.Chart(comp_df)
            .mark_bar()
            .encode(x=alt.X("Chỉ số:N", sort=None), y="Giá trị:Q", column=alt.Column("Tuần:N", sort=["Tuần trước","Tuần này"]),
                    tooltip=["Tuần","Chỉ số","Giá trị"]) 
            .properties(height=280)
            .resolve_scale(y='independent')
        )
        st.altair_chart(chart3, use_container_width=True)
    except Exception:
        import traceback
        st.error("Lỗi khi vẽ biểu đồ So sánh tuần")
        st.code(traceback.format_exc())

    st.markdown("---")
    try:
        c1, c2, c3, c4 = st.columns(4)
        with c1: kpi("Lượt điều trị (tuần này)", treatment_this)
        with c2: kpi("Ra viện (tuần này)", discharge_this)
        with c3: kpi("CLS (tuần này)", orders_this)
        with c4: kpi("Số ngày điều trị TB/BN", avg_days_this)
    except Exception:
        import traceback
        st.error("Lỗi khi hiển thị KPI")
        st.code(traceback.format_exc())

# ======================
# Đi buồng (đã chuyển sang dùng Modal/Dialog)
# ======================
elif page == "Đi buồng":
    st.title("🚶‍♂️ Đi buồng (Ward round)")

    # ========= Modal: form khám =========
    @st.dialog("🧑‍⚕️ Khám đi buồng")
    def open_round_dialog(patient_id: int):
        p = get_patient_info(patient_id)
        if not p:
            st.error("Không tìm thấy bệnh nhân."); return

        st.markdown(f"**{p['name']}** — {p.get('medical_id') or '—'}  \nPhòng {p.get('ward','')} • Giường {p.get('bed','') or '—'}")
        with st.form(f"form_round_{patient_id}"):
            colA, colB = st.columns([1,1])
            with colA:
                try:
                    visit_day = st.date_input("Ngày khám", value=date.today(), format="DD/MM/YYYY")
                except TypeError:
                    visit_day = st.date_input("Ngày khám", value=date.today())
            with colB:
                operated_now = st.checkbox("Đã phẫu thuật", value=bool(p.get("operated",0)))

            general_status = st.text_area("Tình trạng toàn thân", height=100)
            system_exam    = st.text_area("Khám bộ phận", height=140)
            plan           = st.text_area("Phương án điều trị tiếp", height=120)

            st.markdown("#### 🧪 CLS thêm")
            extra_opts = [f"{t[0]} — {t[1]}" for t in COMMON_TESTS]
            extra_selected = st.multiselect("Chọn CLS", extra_opts)
            extra_note = st.text_area("Diễn giải CLS / Lý do", placeholder="VD: tăng CRP, nghi nhiễm; kiểm tra HbA1c…")
            try:
                extra_scheduled = st.date_input("Ngày dự kiến thực hiện CLS", value=date.today(), format="DD/MM/YYYY")
            except TypeError:
                extra_scheduled = st.date_input("Ngày dự kiến thực hiện CLS", value=date.today())

            b1, b2, b3 = st.columns([1,1,1])
            save_round    = b1.form_submit_button("💾 Lưu khám")
            discharge_now = b3.form_submit_button("🏁 Xuất viện hôm nay")

        if save_round:
            update_patient_operated(patient_id, operated_now)
            round_rec = {
                "patient_id": patient_id,
                "visit_date": visit_day.strftime(DATE_FMT),
                "general_status": general_status.strip(),
                "system_exam": system_exam.strip(),
                "plan": plan.strip(),
                "extra_tests": ", ".join(extra_selected) if extra_selected else "",
                "extra_tests_note": extra_note.strip(),
            }
            add_ward_round(round_rec)

            if extra_selected:
                today_str = date.today().strftime(DATE_FMT)
                sched_str = extra_scheduled.strftime(DATE_FMT)
                text_to_tuple = {f"{t[0]} — {t[1]}": t for t in COMMON_TESTS}
                for sel in extra_selected:
                    ot, desc = text_to_tuple[sel]
                    desc_full = desc if not extra_note.strip() else f"{desc} — {extra_note.strip()}"
                    add_order({
                        "patient_id": patient_id,
                        "order_type": ot,
                        "description": desc_full,
                        "date_ordered": today_str,
                        "scheduled_date": sched_str,
                        "status": "scheduled"
                    })

            st.success("✅ Đã lưu nội dung khám đi buồng")
            st.cache_data.clear()
            safe_rerun()

        if discharge_now:
            discharge_patient(patient_id)
            st.success("🏁 Đã xuất viện.")
            st.cache_data.clear()
            st.session_state.active_page = "Xuất viện"
            st.session_state.discharge_view_date = date.today()
            safe_rerun()

    # ==== TÌM KIẾM NHANH BN (không phân biệt dấu, Enter để tìm) ====
    def _strip_accents(s: str) -> str:
        if not isinstance(s, str):
            return ""
        s = s.lower().strip()
        return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

    st.markdown("### 🔎 Tìm BN nhanh")

    # 1) Nhập & nhấn Enter để tìm
    with st.form("qsearch_form", clear_on_submit=False):
        q_text = st.text_input(
            "Nhập tên (có/không dấu) hoặc mã bệnh án rồi nhấn Enter",
            key="qsearch_text",
            placeholder="VD: hoang kim tuoc hoặc BN001"
        )
        submitted = st.form_submit_button("Tìm")  # Enter trong ô sẽ kích hoạt

    # 2) Xử lý sau khi submit
    if submitted:
        q_norm = _strip_accents(q_text)
        if not q_norm:  # cho phép 1 chữ, nhưng không để rỗng
            st.warning("Bạn chưa nhập nội dung tìm kiếm.")
        else:
            # Lấy danh sách BN đang điều trị (bỏ giường/đã mổ; thêm chẩn đoán)
            df_act = query_df("""
                SELECT id, medical_id, name, ward, diagnosis
                FROM patients
                WHERE active = 1
                ORDER BY name
            """)

            if df_act.empty:
                st.info("Chưa có bệnh nhân đang điều trị.")
            else:
                # Map phương án điều trị tiếp mới nhất cho mọi BN
                plan_map = latest_plan_map_all_patients()

                # Lọc: trùng 1 phần tên (không dấu) hoặc 1 phần mã BA
                results = []
                for r in df_act.to_dict(orient="records"):
                    name_norm = _strip_accents(r.get("name", ""))
                    mid = (r.get("medical_id") or "").lower()
                    if (q_norm in name_norm) or (q_norm in mid):
                        results.append(r)

                # Hiển thị theo BẢNG có thể kéo ngang (phù hợp mobile)
                if not results:
                    st.info("Không tìm thấy bệnh nhân phù hợp.")
                else:
                    st.success(f"Tìm thấy {len(results)} bệnh nhân:")

                    table_rows = []
                    label_map = {}
                    for r in results:
                        pid = int(r["id"])
                        plan_last = plan_map.get(pid, "") or "—"
                        row = {
                            "Họ tên": r.get("name", "—"),
                            "Mã BA": r.get("medical_id") or "—",
                            "Phòng": r.get("ward") or "—",
                            "Chẩn đoán": r.get("diagnosis") or "—",
                            "PA điều trị tiếp": plan_last,
                            "PID": pid,  # để mở Khám
                        }
                        table_rows.append(row)
                        label_map[pid] = f"{row['Họ tên']} — {row['Mã BA']} (P.{row['Phòng']})"

                    df_view = pd.DataFrame(table_rows)
                    st.dataframe(
                        df_view.drop(columns=["PID"]),
                        use_container_width=True,
                        hide_index=True
                    )

                    # Chọn một BN để mở dialog Khám
                    pid_options = [r["PID"] for r in table_rows]
                    if pid_options:
                        selected_pid = st.selectbox(
                            "Chọn bệnh nhân để mở Khám",
                            options=pid_options,
                            format_func=lambda x: label_map.get(int(x), str(x)),
                            key="qsearch_pick_pid"
                        )
                        if st.button("Khám", key="qsearch_open"):
                            open_round_dialog(int(selected_pid))

    st.markdown("---")
    # ==== HẾT - TÌM KIẾM NHANH BN ====

    # ================== Nội dung trang ==================
    wards_df = query_df("SELECT DISTINCT ward FROM patients WHERE active=1 AND ward IS NOT NULL AND ward<>'' ORDER BY ward")
    ward_options = wards_df["ward"].tolist() if not wards_df.empty else []
    sel_ward = st.selectbox("Chọn phòng", ward_options if ward_options else ["(Chưa có phòng)"])

    # === 🗂️ Thư mục trong ngày: Đã khám hôm nay & Nhập mới hôm nay (theo phòng) ===
    if ward_options:
        st.markdown(f"### 🗂️ Thư mục trong ngày — Phòng **{sel_ward}**")
        today_str = date.today().strftime(DATE_FMT)
        colL, colR = st.columns(2)

        # ĐÃ KHÁM HÔM NAY
        df_round_today_full = rounds_latest_today_with_plan()
        if not df_round_today_full.empty:
            df_round_today_full = df_round_today_full[df_round_today_full["ward"] == sel_ward]
            if df_round_today_full.empty:
                with colL:
                    st.markdown("**Đã khám đi buồng hôm nay**")
                    st.info("Chưa có.")
            else:
                df_v1 = df_round_today_full.copy()
                df_v1["Tuổi"] = df_v1["dob"].apply(calc_age)
                df_v1 = df_v1.rename(columns={
                    "name":"Họ và tên","diagnosis":"Chẩn đoán","notes":"Ghi chú","plan":"Phương án điều trị tiếp"
                })
                df_v1 = df_v1[["Họ và tên","Tuổi","Chẩn đoán","Phương án điều trị tiếp","Ghi chú"]]
                with colL:
                    st.markdown("**Đã khám đi buồng hôm nay**")
                    st.dataframe(df_v1, use_container_width=True, hide_index=True)
        else:
            with colL:
                st.markdown("**Đã khám đi buồng hôm nay**")
                st.info("Chưa có.")

        # BN nhập mới hôm nay
        df_new_today = query_df("""
            SELECT id, name, dob, diagnosis, notes, ward, active
            FROM patients
            WHERE admission_date = ? AND active = 1
            ORDER BY name
        """, (today_str,))
        if not df_new_today.empty:
            df_new_today = df_new_today[df_new_today["ward"] == sel_ward]
            if df_new_today.empty:
                with colR:
                    st.markdown("**BN nhập mới hôm nay**")
                    st.info("Chưa có.")
            else:
                df_v2 = df_new_today.copy()
                df_v2["Tuổi"] = df_v2["dob"].apply(calc_age)
                df_v2 = df_v2.rename(columns={"name":"Họ và tên","diagnosis":"Chẩn đoán","notes":"Ghi chú"})
                df_v2 = df_v2[["Họ và tên","Tuổi","Chẩn đoán","Ghi chú"]]
                with colR:
                    st.markdown("**BN nhập mới hôm nay**")
                    st.dataframe(df_v2, use_container_width=True, hide_index=True)
        else:
            with colR:
                st.markdown("**BN nhập mới hôm nay**")
                st.info("Chưa có.")

        st.markdown("---")

    # DANH SÁCH PHÒNG + Nút KHÁM mở modal
    if ward_options:
        df_room = query_df("SELECT * FROM patients WHERE active=1 AND ward=? ORDER BY bed, name", (sel_ward,))
        if df_room.empty:
            st.info("Phòng này chưa có BN đang điều trị.")
        else:
            st.subheader(f"📋 Danh sách BN phòng {sel_ward}")

            # Map phương án điều trị mới nhất (mọi ngày) cho từng BN
            plan_map = latest_plan_map_all_patients()

            table_rows = []
            for r in df_room.to_dict(orient="records"):
                age = calc_age(r.get("dob"))
                d_in = days_between(r.get("admission_date"))
                table_rows.append({
                    "Mã BA": r["medical_id"],
                    "Họ tên": r["name"],
                    "Tuổi": age if age is not None else "",
                    "Chẩn đoán": r.get("diagnosis","") or "",
                    "Số ngày điều trị": d_in if d_in is not None else "",
                    "Đã PT": "✅" if r.get("operated",0)==1 else "✗",
                    "PA điều trị tiếp": plan_map.get(int(r["id"]), "") or "",
                    "Ghi chú": r.get("notes","") or "",
                    "ID": r["id"],
                })
            df_view = pd.DataFrame(table_rows)
            st.dataframe(df_view.drop(columns=["ID"]), use_container_width=True, hide_index=True)

            st.markdown("### Khám tại giường")
            for r in df_room.to_dict("records"):
                c = st.columns([3,1,1,1,2,1,1])
                age = calc_age(r.get("dob"))
                plan_last = plan_map.get(int(r["id"]), "")
                c[0].markdown(
                    f"**{r['name']}** — {r['medical_id']}  "
                    f"<br/><span class='small'>Chẩn đoán: {r.get('diagnosis','')}</span>"
                    + (f"<br/><span class='small'>PA điều trị: {plan_last}</span>" if plan_last else ""),
                    unsafe_allow_html=True
                )
                d_in = days_between(r.get("admission_date"))
                c[1].markdown(f"Tuổi: **{age if age is not None else ''}**")
                c[2].markdown(f"Ngày điều trị: **{d_in if d_in is not None else ''}**")
                c[3].markdown("Đã PT: **✅**" if r.get("operated",0)==1 else "Đã PT: **✗**")
                c[4].markdown(f"<span class='small'>{r.get('notes','')}</span>", unsafe_allow_html=True)
                if c[5].button("Khám", key=f"round_{r['id']}"):
                    open_round_dialog(int(r["id"]))  # mở modal
                if c[6].button("✏️ Sửa", key=f"round_edit_{r['id']}"):
                    go_edit(r["id"])

    # LỊCH SỬ KHÁM (xem lại)
    st.markdown("---")
    st.markdown("### 📅 Lịch sử khám")
    all_active = query_df("SELECT id, name FROM patients WHERE active=1 ORDER BY name")
    if all_active.empty:
        st.info("Chưa có BN đang điều trị để xem lịch sử.")
    else:
        pid_hist = st.selectbox("Chọn BN để xem lịch sử", options=all_active["id"],
                                format_func=lambda x: f"{all_active[all_active['id']==x]['name'].values[0]}")
        if pid_hist:
            hist_days = query_df("SELECT DISTINCT visit_date FROM ward_rounds WHERE patient_id=? ORDER BY visit_date DESC", (int(pid_hist),))
            if hist_days.empty:
                st.info("BN này chưa có lịch sử đi buồng.")
            else:
                day_strs = hist_days["visit_date"].tolist()
                sel_hist = st.selectbox("Chọn ngày để xem lại", day_strs)
                hist = query_df("""
                    SELECT * FROM ward_rounds
                    WHERE patient_id=? AND visit_date=?
                    ORDER BY id DESC
                """, (int(pid_hist), sel_hist))
                for _, r in hist.iterrows():
                    st.markdown(f"**Lần ghi #{r['id']} — {r['visit_date']}**")
                    st.write("**Tình trạng toàn thân:**", r["general_status"] or "—")
                    st.write("**Khám bộ phận:**", r["system_exam"] or "—")
                    st.write("**Phương án điều trị:**", r["plan"] or "—")
                    if r["extra_tests"]:
                        st.write("**CLS thêm:**", r["extra_tests"])
                    if r["extra_tests_note"]:
                        st.write("**Diễn giải CLS:**", r["extra_tests_note"])
                    st.caption(f"🕒 Tạo lúc: {r['created_at']}")
                    st.markdown("---")

# ======================
# Lịch XN/Chụp
# ======================
elif page == "Lịch XN/Chụp":
    st.title("🧪 Lịch xét nghiệm & chụp chiếu")

    df_orders = query_df("""
        SELECT o.*, p.name as patient_name, p.ward
        FROM orders o LEFT JOIN patients p ON o.patient_id=p.id
    """)
    if df_orders.empty:
        st.info("Chưa có chỉ định nào.")
    else:
        filter_choice = st.selectbox("Xem", ["Hôm nay", "7 ngày tới", "Tất cả"], index=0)
        today_str = date.today().strftime(DATE_FMT)
        if filter_choice == "Hôm nay":
            df_view = df_orders[df_orders["scheduled_date"]==today_str]
        elif filter_choice == "7 ngày tới":
            end = (date.today()+timedelta(days=7)).strftime(DATE_FMT)
            df_view = df_orders[(df_orders["scheduled_date"]>=today_str) & (df_orders["scheduled_date"]<=end)]
        else:
            df_view = df_orders.copy()

        for od in df_view.sort_values(["scheduled_date"]).to_dict(orient="records"):
            st.markdown(f"**{od['patient_name']}** — {od['order_type']} — {od.get('description','')}")
            st.caption(f"Đặt: {od.get('date_ordered')} | Dự kiến: {od.get('scheduled_date')} | Trạng thái: {od.get('status')}")
            col1, col2 = st.columns([3,1])
            with col1:
                result_text = st.text_input(f"Kết quả (Order {od['id']})", key=f"res_{od['id']}")
            with col2:
                if st.button("Đánh dấu đã làm", key=f"done_{od['id']}"):
                    mark_order_done(od["id"], result_text)
                    st.success("✅ Đã đánh dấu hoàn thành")
                    st.cache_data.clear()
                    safe_rerun()
        st.dataframe(
            df_view[["id","patient_name","ward","order_type","description","date_ordered","scheduled_date","status","result_date"]],
            use_container_width=True, hide_index=True
        )

    st.subheader("Thêm chỉ định mới")
    patients_df = query_df("SELECT id, medical_id, name, ward FROM patients WHERE active=1 ORDER BY ward, name")
    if not patients_df.empty:
        with st.form("form_add_order", clear_on_submit=True):
            pid = st.selectbox(
                "Chọn BN",
                options=patients_df["id"],
                format_func=lambda x: f"{patients_df[patients_df['id']==x]['medical_id'].values[0]} - {patients_df[patients_df['id']==x]['name'].values[0]} ({patients_df[patients_df['id']==x]['ward'].values[0]})"
            )
            custom_types = ["XN máu","X-quang","CT","Siêu âm","Khác"]
            order_type = st.selectbox("Loại", sorted(set(custom_types + [t[0] for t in COMMON_TESTS])))
            desc = st.text_area("Mô tả")
            try:
                scheduled = st.date_input("Ngày dự kiến", value=date.today(), format="DD/MM/YYYY")
            except TypeError:
                scheduled = st.date_input("Ngày dự kiến", value=date.today())
            submitted2 = st.form_submit_button("➕ Thêm chỉ định")
            if submitted2:
                add_order({
                    "patient_id": int(pid),
                    "order_type": order_type,
                    "description": desc.strip(),
                    "date_ordered": date.today().strftime(DATE_FMT),
                    "scheduled_date": scheduled.strftime(DATE_FMT),
                    "status":"scheduled"
                })
                st.success("✅ Thêm chỉ định thành công")
                st.cache_data.clear()
                safe_rerun()
    else:
        st.info("Không có BN đang điều trị để thêm chỉ định.")

# ======================
# Trang XUẤT VIỆN
# ======================
elif page == "Xuất viện":
    st.title("🏁 Xuất viện")

    if "discharge_view_date" not in st.session_state:
        st.session_state.discharge_view_date = date.today()

    today_date = date.today()
    today_str = today_date.strftime(DATE_FMT)
    df_today = query_df("SELECT * FROM patients WHERE discharge_date = ? ORDER BY ward, name", (today_str,))
    st.subheader(f"Hôm nay ({today_date.strftime('%d/%m/%Y')})")
    st.write(f"Số bệnh nhân xuất viện: **{len(df_today)}**")

    def _render_discharge_list(df_src: pd.DataFrame, key_prefix: str):
        if df_src.empty:
            st.info("Không có bệnh nhân.")
            return
        df_show = df_src.copy()
        df_show["Số ngày điều trị"] = df_show.apply(lambda r: days_between(r["admission_date"], r["discharge_date"]), axis=1)
        for col in ["discharge_time", "discharge_prescription", "discharge_advice"]:
            if col not in df_show.columns:
                df_show[col] = ""
        st.dataframe(
            df_show[["medical_id","name","ward","bed","admission_date","discharge_date","discharge_time","diagnosis","notes","Số ngày điều trị","discharge_prescription","discharge_advice","surgery_needed","operated"]].rename(columns={
                "medical_id":"Mã BA","name":"Họ tên","ward":"Phòng","bed":"Giường",
                "admission_date":"Ngày NV","discharge_date":"Ngày XV","discharge_time":"Giờ ra",
                "diagnosis":"Chẩn đoán","notes":"Ghi chú","discharge_prescription":"Đơn thuốc",
                "discharge_advice":"Tư vấn","surgery_needed":"Cần mổ","operated":"Đã PT"
            }),
            use_container_width=True, hide_index=True
        )
        st.caption("Bấm ↩️ Quay lại để hủy xuất viện và chuyển BN về danh sách đang điều trị.")
        for r in df_src.to_dict(orient="records"):
            cols = st.columns([3,2,2,2,1])
            cols[0].markdown(f"**{r['name']}** — {r.get('medical_id') or '—'}")
            cols[1].markdown(f"Phòng {r.get('ward','')} • Giường {r.get('bed','') or '—'}")
            out_time = r.get("discharge_time") or r.get("discharge_date") or ""
            cols[2].markdown(f"NV: {r.get('admission_date','')} → Ra: {out_time}")
            cols[3].markdown(f"<span class='small'>CD: {r.get('diagnosis','')}</span>", unsafe_allow_html=True)
            if cols[4].button("↩️ Quay lại", key=f"{key_prefix}_undo_{r['id']}"):
                undo_discharge(r["id"])
                st.success(f"Đã chuyển {r['name']} về BN đang điều trị.")
                st.session_state.active_page = "Trang chủ"
                st.cache_data.clear()
                safe_rerun()
        return df_show

    df_today_show = _render_discharge_list(df_today, "today")
    if not df_today.empty:
        if st.button("⬇️ Xuất Excel — Hôm nay"):
            xls = export_excel({"discharges_today": df_today_show})
            st.download_button("Tải file xuất viện hôm nay", data=xls.getvalue(),
                               file_name=f"discharges_{today_str}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.markdown("---")
    st.subheader("Xuất viện theo ngày khác")
    try:
        pick_date = st.date_input("Chọn ngày", value=st.session_state.discharge_view_date, format="DD/MM/YYYY")
    except TypeError:
        pick_date = st.date_input("Chọn ngày", value=st.session_state.discharge_view_date)
    st.session_state.discharge_view_date = pick_date
    pick_str = pick_date.strftime(DATE_FMT)
    df_pick = query_df("SELECT * FROM patients WHERE discharge_date = ? ORDER BY ward, name", (pick_str,))
    st.write(f"Số bệnh nhân xuất viện ngày {pick_date.strftime('%d/%m/%Y')}: **{len(df_pick)}**")

    df_pick_show = _render_discharge_list(df_pick, "pick")
    if not df_pick.empty:
        if st.button("⬇️ Xuất Excel — Ngày đã chọn"):
            xls2 = export_excel({"discharges_on": df_pick_show})
            st.download_button("Tải file xuất viện ngày đã chọn", data=xls2.getvalue(),
                               file_name=f"discharges_{pick_str}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ======================
# Trang LỊCH TRỰC (mới)
# ======================
elif page == "Lịch trực":
    st.title("📅 Lịch trực")

    def _sanitize_filename(name: str) -> str:
        base = pathlib.Path(name).name
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", base)

    def _save_uploaded(scope: str, up):
        raw_name = _sanitize_filename(up.name)
        ext = pathlib.Path(raw_name).suffix.lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"{scope}_{ts}_{raw_name}"
        full_path = os.path.join(DUTY_DIR, final_name)
        with open(full_path, "wb") as f:
            f.write(up.getbuffer())
        mime = up.type or mimetypes.guess_type(raw_name)[0] or "application/octet-stream"
        _exec("INSERT INTO duty_files(scope, filename, mime, path, uploaded_at) VALUES (?,?,?,?,?)",
              (scope, raw_name, mime, full_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        st.success("✅ Đã tải lên.")
        st.cache_data.clear()  # refresh query_df cache

    def _embed_pdf_from_path(path: str):
        try:
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("utf-8")
            html = f"""
            <div class="embed">
              <embed src="data:application/pdf;base64,{b64}" type="application/pdf" width="100%" height="100%"/>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Không thể hiển thị PDF: {e}")

    def _show_file_row(rec):
        path = rec["path"]; mime = rec["mime"] or mimetypes.guess_type(rec["filename"])[0] or ""
        st.write(f"**{rec['filename']}**  \n<span class='small'>Tải lên: {rec['uploaded_at']}</span>", unsafe_allow_html=True)
        if os.path.exists(path):
            if mime.startswith("image/"):
                st.image(path, use_container_width=True)
            elif mime in ("application/pdf", "application/x-pdf"):
                _embed_pdf_from_path(path)
            elif mime in ("text/csv", "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
                try:
                    if mime == "text/csv":
                        df = pd.read_csv(path)
                    else:
                        df = pd.read_excel(path)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                except Exception as e:
                    st.error(f"Không đọc được bảng: {e}")
            else:
                st.info("Định dạng không hỗ trợ xem trực tiếp. Bạn có thể tải xuống.")
            try:
                with open(path, "rb") as f:
                    bytes_data = f.read()
                st.download_button("⬇️ Tải tệp", data=bytes_data, file_name=rec["filename"])
            except Exception as e:
                st.error(f"Không thể tạo nút tải xuống: {e}")
        else:
            st.error("Tệp đã bị xóa trên máy chủ.")

    tabs = st.tabs(["🏥 Lịch trực bệnh viện", "🏨 Lịch trực khoa"])

    # ---- Lịch trực bệnh viện
    with tabs[0]:
        st.subheader("Tải lịch trực bệnh viện")
        up = st.file_uploader("Chọn tệp (PDF/Ảnh/CSV/XLSX...)", type=None, key="duty_hospital")
        if up is not None:
            if st.button("📤 Tải lên — Bệnh viện"):
                _save_uploaded("hospital", up)
                safe_rerun()
        st.markdown("---")
        st.subheader("Xem lịch trực bệnh viện")
        df_files = query_df("SELECT * FROM duty_files WHERE scope='hospital' ORDER BY uploaded_at DESC")
        if df_files.empty:
            st.info("Chưa có tệp lịch trực bệnh viện.")
        else:
            for _, rec in df_files.iterrows():
                with st.expander(f"📄 {rec['filename']}  —  {rec['uploaded_at']}", expanded=False):
                    _show_file_row(rec)

    # ---- Lịch trực khoa
    with tabs[1]:
        st.subheader("Tải lịch trực khoa")
        up2 = st.file_uploader("Chọn tệp (PDF/Ảnh/CSV/XLSX...)", type=None, key="duty_department")
        if up2 is not None:
            if st.button("📤 Tải lên — Khoa"):
                _save_uploaded("department", up2)
                safe_rerun()
        st.markdown("---")
        st.subheader("Xem lịch trực khoa")
        df_files2 = query_df("SELECT * FROM duty_files WHERE scope='department' ORDER BY uploaded_at DESC")
        if df_files2.empty:
            st.info("Chưa có tệp lịch trực khoa.")
        else:
            for _, rec in df_files2.iterrows():
                with st.expander(f"📄 {rec['filename']}  —  {rec['uploaded_at']}", expanded=False):
                    _show_file_row(rec)

# ======================
# Tìm kiếm & Lịch sử
# ======================
elif page == "Tìm kiếm & Lịch sử":
    st.title("🔎 Tìm kiếm bệnh nhân")
    q = st.text_input("Tìm theo tên / mã bệnh án / phòng")
    if q:
        q_like = f"%{q.strip()}%"
        df = query_df("""
            SELECT * FROM patients
            WHERE medical_id LIKE ? OR name LIKE ? OR ward LIKE ?
            ORDER BY admission_date DESC
        """, (q_like, q_like, q_like))
        if df.empty:
            st.warning("Không tìm thấy")
        else:
            for r in df.to_dict(orient="records"):
                st.subheader(f"{r['medical_id']} - {r['name']}")
                st.write(f"Phòng: {r.get('ward','')} | Giường: {r.get('bed','')}")
                chandoan = r.get('diagnosis') or ''
                st.write(f"Ngày NV: {r.get('admission_date','')} | Phẫu thuật: {'Có' if r.get('surgery_needed',0)==1 else 'Không'} | Đã mổ: {'Có' if r.get('operated',0)==1 else 'Chưa'} | Active: {r['active']}")
                if chandoan:
                    st.write(f"📝 Chẩn đoán: {chandoan}")
                st.write("Ghi chú:", r.get("notes",""))
                ords = query_df("SELECT * FROM orders WHERE patient_id=? ORDER BY scheduled_date DESC", (r["id"],))
                if not ords.empty:
                    st.table(ords[["order_type","description","scheduled_date","status","result_date"]])
                else:
                    st.write("Chưa có chỉ định.")

                col1, col2, col3 = st.columns([1, 1, 1])
                if col1.button("✏️ Chỉnh sửa", key=f"edit_{r['id']}"):
                    go_edit(r['id'])
                if col2.button("🗑️ Xóa", key=f"delete_{r['id']}"):
                    _exec("DELETE FROM patients WHERE id=?", (r['id'],))
                    st.success(f"Đã xóa bệnh nhân {r['name']}")
                    st.cache_data.clear()
                    safe_rerun()
                if col3.button("Xuất viện", key=f"dis2_{r['id']}"):
                    discharge_patient(r["id"])
                    st.success("✅ Đã xuất viện")
                    st.cache_data.clear()
                    safe_rerun()

# ======================
# Chỉnh sửa BN
# ======================
elif page == "Chỉnh sửa BN":
    st.title("✏️ Chỉnh sửa bệnh nhân")

    show_only_active = st.checkbox("Chỉ hiển thị BN đang điều trị (active=1)", value=True)
    name_query = st.text_input("Tìm theo tên/mã bệnh án (gõ để lọc nhanh)")

    if show_only_active:
        df_pat = query_df("SELECT id, medical_id, name, ward FROM patients WHERE active=1 ORDER BY ward, name")
    else:
        df_pat = query_df("SELECT id, medical_id, name, ward FROM patients ORDER BY active DESC, ward, name")

    if not df_pat.empty and name_query:
        q = f"%{name_query.strip()}%"
        df_pat = query_df(
            """
            SELECT id, medical_id, name, ward FROM patients
            WHERE (medical_id LIKE ? OR name LIKE ?) AND (? = 1 OR active = 1)
            ORDER BY ward, name
            """,
            (q, q, 1 if show_only_active else 0)
        )

    if df_pat.empty:
        st.info("Chưa có bệnh nhân phù hợp để chỉnh sửa.")
        st.stop()

    options = df_pat["id"].tolist()
    if "edit_patient_id" in st.session_state and st.session_state.edit_patient_id in options:
        default_index = options.index(int(st.session_state.edit_patient_id))
    else:
        default_index = 0

    pid = st.selectbox(
        "Chọn bệnh nhân",
        options=options,
        index=default_index,
        format_func=lambda x: f"{df_pat[df_pat['id']==x]['medical_id'].values[0] or '—'} - {df_pat[df_pat['id']==x]['name'].values[0]} (Phòng {df_pat[df_pat['id']==x]['ward'].values[0] or '—'})",
        key="edit_select_pid"
    )

    info_df = query_df("SELECT * FROM patients WHERE id=?", (int(pid),))
    if info_df.empty:
        st.error("Không tìm thấy bệnh nhân.")
        st.stop()
    p = info_df.iloc[0].to_dict()

    st.markdown("---")
    st.subheader(f"Đang chỉnh sửa: **{p.get('medical_id') or '—'} — {p.get('name', '')}**")

    def _safe_date(s: Optional[str], fallback: date) -> date:
        try:
            return datetime.strptime(s, DATE_FMT).date() if s else fallback
        except Exception:
            return fallback

    admission_default  = _safe_date(p.get("admission_date"), date.today())
    discharge_default  = _safe_date(p.get("discharge_date"), date.today()) if p.get("discharge_date") else None

    with st.form("form_edit_patient_full"):
        col1, col2, col3 = st.columns(3)
        with col1:
            medical_id = st.text_input("Mã bệnh án", value=p.get("medical_id") or "")
            name       = st.text_input("Họ tên *", value=p.get("name") or "")
        with col2:
            ward       = st.text_input("Phòng", value=p.get("ward") or "")
            bed        = st.text_input("Giường", value=p.get("bed") or "")
        with col3:
            surgery_needed = st.checkbox("Cần phẫu thuật?", value=bool(p.get("surgery_needed",0)))
            operated       = st.checkbox("Đã phẫu thuật", value=bool(p.get("operated",0)))

        try:
            admission_date = st.date_input("Ngày nhập viện", value=admission_default, format="DD/MM/YYYY")
        except TypeError:
            admission_date = st.date_input("Ngày nhập viện", value=admission_default)

        discharge_enable = st.checkbox("Có ngày xuất viện?", value=bool(discharge_default))
        if discharge_enable:
            try:
                discharge_date = st.date_input("Ngày xuất viện", value=discharge_default or date.today(), format="DD/MM/YYYY")
            except TypeError:
                discharge_date = st.date_input("Ngày xuất viện", value=discharge_default or date.today())
        else:
            discharge_date = None

        diagnosis = st.text_input("📝 Chẩn đoán", value=p.get("diagnosis") or "")
        notes     = st.text_area("Ghi chú", value=p.get("notes") or "")

        c_save, c_dis, c_del = st.columns([1,1,1])
        submitted = c_save.form_submit_button("💾 Lưu thay đổi")
        do_discharge = c_dis.form_submit_button("🏁 Xuất viện (set active=0)")
        do_delete = c_del.form_submit_button("🗑️ Xoá bệnh nhân")

    if submitted:
        if not name.strip():
            st.error("Vui lòng nhập Họ tên."); st.stop()
        _exec(
            """
            UPDATE patients
            SET medical_id=?, name=?, ward=?, bed=?,
                admission_date=?, discharge_date=?,
                surgery_needed=?, operated=?,
                diagnosis=?, notes=?
            WHERE id=?
            """,
            (
                medical_id.strip() or None,
                name.strip(),
                ward.strip(),
                bed.strip(),
                admission_date.strftime(DATE_FMT),
                discharge_date.strftime(DATE_FMT) if discharge_date else None,
                1 if surgery_needed else 0,
                1 if operated else 0,
                diagnosis.strip(),
                notes.strip(),
                int(pid),
            )
        )
        st.success("✅ Đã lưu thay đổi.")
        st.cache_data.clear()

    if do_discharge:
        discharge_patient(int(pid))
        st.success("✅ Đã xuất viện.")
        st.cache_data.clear()

    if do_delete:
        _exec("DELETE FROM patients WHERE id=?", (int(pid),))
        st.success("🗑️ Đã xoá bệnh nhân.")
        st.cache_data.clear()

# ======================
# Nhập viện mới
# ======================
elif page == "Nhập viện mới":
    st.title("🧾 Nhập bệnh nhân mới")
    today_year = date.today().year
    st.caption("Ưu tiên nhập nhanh: thông tin tối thiểu, chẩn đoán, hướng xử trí và CLS ban đầu.")

    with st.form("form_add_patient", clear_on_submit=True):
        st.markdown("#### Thông tin tối thiểu")
        c1, c2, c3, c4 = st.columns([1,1.5,1,1])

        with c1:
            medical_id = st.text_input("Mã bệnh án (không bắt buộc)")
        with c2:
            name = st.text_input("Họ tên *", value="")
        with c3:
            ward = st.text_input("Phòng")
        with c4:
            bed = st.text_input("Giường")

        age_mode = st.radio("Tuổi / ngày sinh", ["Nhập tuổi", "Nhập năm sinh", "Nhập ngày sinh chi tiết"], horizontal=True)
        dob_age = None
        dob_year = None
        dob_date = None
        if age_mode == "Nhập tuổi":
            dob_age = st.number_input("Tuổi", min_value=0, max_value=130, value=50, step=1)
            st.caption(f"Tạm quy đổi năm sinh: **{today_year - int(dob_age)}**")
        elif age_mode == "Nhập năm sinh":
            dob_year = st.number_input("Năm sinh", min_value=1900, max_value=today_year, value=1980, step=1)
            st.caption(f"Tuổi gần đúng: **{today_year - int(dob_year)}**")
        else:
            try:
                dob_date = st.date_input("Ngày sinh", value=date(1980,1,1), format="DD/MM/YYYY")
            except TypeError:
                dob_date = st.date_input("Ngày sinh", value=date(1980,1,1))

        try:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today(), format="DD/MM/YYYY")
        except TypeError:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today())

        st.markdown("#### Chẩn đoán và hướng xử trí")
        disease_group = st.selectbox(
            "Nhóm bệnh / tình huống",
            ["Chưa phân loại", "Chờ mổ", "Sau mổ", "Chấn thương", "U não/cột sống", "Thoái hóa cột sống", "Nhiễm trùng", "Theo dõi nội khoa", "Khác"],
        )
        diagnosis = st.text_input("Chẩn đoán chính *", value="", placeholder="VD: U màng não / Thoát vị đĩa đệm / Chấn thương sọ não...")
        treatment_mode = st.radio("Kế hoạch chính", ["Nằm theo dõi", "Chuẩn bị mổ", "Sau mổ", "Có thể ra viện sớm"], horizontal=True)

        default_days = 3
        if treatment_mode == "Chuẩn bị mổ":
            default_days = 7
        elif treatment_mode == "Sau mổ":
            default_days = 5
        elif disease_group in ["Chấn thương", "Nhiễm trùng"]:
            default_days = 7
        planned_treatment_days = st.number_input("Số ngày điều trị dự kiến", min_value=0, value=default_days)
        surgery_needed = treatment_mode == "Chuẩn bị mổ" or st.checkbox("Đánh dấu cần phẫu thuật")
        operated = treatment_mode == "Sau mổ" or st.checkbox("Đã phẫu thuật trước khi nhập/nhận vào")

        cnote1, cnote2 = st.columns(2)
        with cnote1:
            meds = st.text_area("Thuốc/y lệnh chính ban đầu", height=90)
        with cnote2:
            notes = st.text_area("Ghi chú cần nhớ", height=90, placeholder="Dị ứng, bệnh nền, nguy cơ, hẹn mổ...")

        st.markdown("---")
        st.subheader("🧪 CLS ban đầu")

        options = [f"{t[0]} — {t[1]}" for t in COMMON_TESTS]
        default_initial = []
        if treatment_mode in ["Chuẩn bị mổ", "Sau mổ"]:
            default_initial = ["XN máu — Tổng phân tích tế bào máu", "XN máu — Sinh hoá cơ bản", "XN máu — Đông máu"]
        if disease_group in ["Chấn thương", "U não/cột sống"] and "CT — CT sọ não không cản quang" in options:
            default_initial.append("CT — CT sọ não không cản quang")
        default_initial = [x for x in default_initial if x in options]
        selected = st.multiselect("Chọn nhanh các chỉ định cần làm", options, default=default_initial)
        try:
            scheduled_all = st.date_input("Ngày dự kiến thực hiện (áp dụng cho tất cả mục đã chọn)", value=date.today(), format="DD/MM/YYYY")
        except TypeError:
            scheduled_all = st.date_input("Ngày dự kiến thực hiện (áp dụng cho tất cả mục đã chọn)", value=date.today())

        submitted = st.form_submit_button("Lưu BN và về màn hình buổi sáng")
        if submitted:
            if age_mode == "Nhập ngày sinh chi tiết" and dob_date:
                dob_final = dob_date
            elif age_mode == "Nhập tuổi" and dob_age is not None:
                yr = max(1900, min(today_year, today_year - int(dob_age)))
                dob_final = date(yr, 1, 1)
            else:
                dob_final = date(int(dob_year or 1980), 1, 1)

            if not name.strip():
                st.error("Vui lòng nhập tối thiểu Họ tên.")
            elif not diagnosis.strip():
                st.error("Vui lòng nhập chẩn đoán chính.")
            else:
                final_notes = notes.strip()
                if disease_group != "Chưa phân loại":
                    final_notes = f"[{disease_group}] {final_notes}".strip()
                patient = {
                    "medical_id": medical_id.strip() if medical_id else None,
                    "name": name.strip(),
                    "dob": dob_final.strftime(DATE_FMT),
                    "ward": ward.strip(),
                    "bed": bed.strip(),
                    "admission_date": admission_date_ui.strftime(DATE_FMT),
                    "severity": None,
                    "surgery_needed": surgery_needed,
                    "planned_treatment_days": int(planned_treatment_days),
                    "meds": meds.strip(),
                    "notes": final_notes,
                    "diagnosis": diagnosis.strip(),
                    "operated": operated,
                }
                new_id = add_patient(patient)

                if selected:
                    today_str = date.today().strftime(DATE_FMT)
                    scheduled_str = scheduled_all.strftime(DATE_FMT)
                    text_to_tuple = {f"{t[0]} — {t[1]}": t for t in COMMON_TESTS}
                    for sel in selected:
                        ot, desc = text_to_tuple[sel]
                        add_order({
                            "patient_id": new_id,
                            "order_type": ot,
                            "description": desc,
                            "date_ordered": today_str,
                            "scheduled_date": scheduled_str,
                            "status": "scheduled"
                        })

                st.success(
                    f"Đã thêm BN • DOB: {dob_final.strftime('%d/%m/%Y')} • Nhập viện: {admission_date_ui.strftime('%d/%m/%Y')}"
                    + (f" • Đã tạo {len(selected)} CLS" if selected else "")
                )
                st.cache_data.clear()
                st.session_state.active_page = "Buổi sáng"
                safe_rerun()

# ======================
# Báo cáo
# ======================
elif page == "Báo cáo":
    st.title("📑 Báo cáo")

    st.subheader("Báo cáo nhanh theo ngày")
    day = st.date_input("Chọn ngày báo cáo", value=date.today())
    dstr = day.strftime(DATE_FMT)
    patients_on_day = query_df("""
        SELECT * FROM patients
        WHERE admission_date <= ? AND (discharge_date IS NULL OR discharge_date >= ?)
    """, (dstr, dstr))
    orders_day = query_df("""
        SELECT o.*, p.name FROM orders o
        LEFT JOIN patients p ON o.patient_id=p.id
        WHERE o.scheduled_date = ?
    """, (dstr,))
    st.write(f"BN có mặt ngày {dstr}: **{len(patients_on_day)}**")
    st.write(f"Chỉ định scheduled cho ngày {dstr}: **{len(orders_day)}**")
    st.dataframe(orders_day[["patient_id","name","order_type","description","status"]], use_container_width=True, hide_index=True)

    if st.button("⬇️ Xuất báo cáo ngày (Excel)"):
        xls = export_excel({"patients_on_day": patients_on_day, "orders_day": orders_day})
        st.download_button("Tải file báo cáo ngày", data=xls.getvalue(),
                           file_name=f"report_{dstr}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.markdown("---")
    st.subheader("Báo cáo tháng")
    ym = st.date_input("Chọn ngày thuộc tháng muốn báo cáo", value=date.today())
    first = date(ym.year, ym.month, 1).strftime(DATE_FMT)
    next_month = ym.replace(day=28) + timedelta(days=4)
    last_day = (next_month - timedelta(days=next_month.day)).strftime(DATE_FMT)
    patients_month = query_df("SELECT * FROM patients WHERE admission_date BETWEEN ? AND ?", (first, last_day))
    st.write(f"Tổng BN nhập trong tháng {ym.month}/{ym.year}: **{len(patients_month)}**")
    if st.button("⬇️ Xuất báo cáo tháng (Excel)"):
        xls = export_excel({"patients_month": patients_month})
        st.download_button("Tải file báo cáo tháng", data=xls.getvalue(),
                           file_name=f"report_month_{ym.year}_{ym.month}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ======================
# Cài đặt / Demo
# ======================
elif page == "Cài đặt / Demo":
    st.title("⚙️ Cài đặt & Demo")
    st.write("- Chạy ứng dụng: `streamlit run app.py --server.address 0.0.0.0 --server.port 8501`")
    st.write("- Bật mật khẩu (khuyên dùng khi mở mạng): `export APP_PASSWORD=yourpass` (Linux/Mac) hoặc `set APP_PASSWORD=yourpass` (Windows)")
    st.write("- File cơ sở dữ liệu:", DB_PATH)

    st.subheader("Dọn dữ liệu bệnh nhân cũ")
    st.warning("Chỉ dùng khi anh muốn làm sạch danh sách bệnh nhân cũ/demo. Thao tác này xóa bệnh nhân, khám đi buồng, CLS và liên kết tệp trong database.")
    confirm_reset = st.text_input("Gõ XOA để xác nhận xóa dữ liệu cũ", key="confirm_reset_clinical")
    if st.button("Xóa toàn bộ dữ liệu bệnh nhân cũ"):
        if confirm_reset.strip().upper() == "XOA":
            reset_clinical_data()
            st.cache_data.clear()
            st.success("Đã xóa sạch dữ liệu bệnh nhân cũ. Có thể bắt đầu nhập bệnh nhân mới.")
            safe_rerun()
        else:
            st.error("Chưa xác nhận. Vui lòng gõ XOA trước khi bấm xóa.")

    # Banner upload / delete
    st.subheader("🖼️ Quản lý banner trang chủ")
    st.markdown("Bạn có thể tải ảnh banner (PNG/JPG/GIF). Ảnh sẽ được lưu vào thư mục `static/` và tự động hiển thị trên Trang chủ.")

    # Tạo thư mục static nếu chưa có
    try:
        os.makedirs("static", exist_ok=True)
    except Exception:
        pass

    # Hiển thị banner hiện tại (nếu có)
    banner_paths = [os.path.join("static", f) for f in ("banner.png", "banner.jpg", "banner.jpeg", "banner.gif")] 
    current_banner = next((p for p in banner_paths if os.path.exists(p)), None)
    if current_banner:
        st.write("Banner hiện tại:")
        try:
            st.image(current_banner, use_container_width=True)
        except Exception:
            st.markdown(f"![banner]({current_banner})")

    uploaded = st.file_uploader("Tải ảnh lên (PNG/JPG/GIF)", type=["png", "jpg", "jpeg", "gif"])
    if uploaded is not None:
        # lưu file với hậu tố gốc, nhưng tiêu chuẩn hóa tên là banner.ext
        ext = os.path.splitext(uploaded.name)[1].lower() or ".png"
        save_name = os.path.join("static", f"banner{ext}")
        # xóa các banner cũ khác định dạng
        for p in banner_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        try:
            with open(save_name, "wb") as f:
                f.write(uploaded.getbuffer())
            st.success("✅ Đã tải lên banner")
            safe_rerun()
        except Exception as e:
            st.error(f"Không thể lưu file: {e}")

    if current_banner:
        if st.button("🗑️ Xóa banner hiện tại"):
            removed = 0
            for p in banner_paths:
                try:
                    if os.path.exists(p):
                        os.remove(p); removed += 1
                except Exception:
                    pass
            if removed:
                st.success("Đã xóa banner")
            else:
                st.info("Không tìm thấy file để xóa")
            safe_rerun()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Tạo dữ liệu mẫu (demo)"):
            load_sample_data()
            st.success("✅ Đã thêm sample data")
            safe_rerun()
    with c2:
        if st.button("Tạo backup ngay (tải file .db)"):
            if not os.path.exists(DB_PATH):
                st.error("Chưa có DB để tải.")
            else:
                with open(DB_PATH, "rb") as f:
                    data = f.read()
                st.download_button("Tải file DB", data=data, file_name=DB_PATH, mime="application/x-sqlite3")
# kết thúc
