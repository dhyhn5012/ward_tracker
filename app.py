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
DATE_FMT = "%Y-%m-%d"
APP_PASSWORD = os.getenv("APP_PASSWORD", "")  # để trống thì tắt password
st.set_page_config(page_title="Bác sĩ Trực tuyến - Theo dõi bệnh nhân", layout="wide", page_icon="🩺")

# ======================
# CSS tinh gọn giao diện
# ======================
CUSTOM_CSS = """
<style>
.kpi {padding:16px;border-radius:16px;background:var(--background-color);box-shadow:0 2px 10px rgba(0,0,0,0.05);border:1px solid rgba(0,0,0,0.05)}
.kpi h3{margin:0;font-size:0.9rem;color:var(--text-color-secondary)}
.kpi .v{font-weight:700;font-size:1.6rem;margin-top:6px}
:root{--text-color-secondary:#6b7280;--background-color: rgba(255,255,255,0.6)}
[data-theme="dark"] :root{--text-color-secondary:#9ca3af;--background-color:rgba(255,255,255,0.04)}
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
hr {margin: 0.6rem 0 1rem 0;}
.small {font-size: 0.9rem; color: var(--text-color-secondary);}
.badge {display:inline-block;padding:2px 8px;border-radius:999px;font-size:0.75rem;border:1px solid rgba(0,0,0,0.1)}
.badge.ok {background:#e8f5e9}
.badge.warn {background:#fff8e1}
.badge.danger {background:#ffebee}
.embed {width:100%; height:720px; border:1px solid rgba(0,0,0,0.08); border-radius:12px; overflow:hidden}
.st-emotion-cache-1dp5vir {z-index: 1000;} /* đảm bảo dialog nổi trên cùng */
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

def discharge_patient(patient_id: int) -> None:
    now = date.today().strftime(DATE_FMT)
    _exec("UPDATE patients SET discharge_date=?, active=0 WHERE id=?", (now, patient_id))

def undo_discharge(patient_id: int) -> None:
    _exec("UPDATE patients SET discharge_date=NULL, active=1 WHERE id=?", (patient_id,))

@st.cache_data(ttl=30, show_spinner=False)
def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)

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
    "Trang chủ",
    "Tổng quan",
    "Nhập BN",
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
    st.session_state.active_page = "Trang chủ"
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
    total_active = len(df_active)
    patients_per_ward = (
        df_active.groupby("ward").size().reset_index(name="Số BN").sort_values("Số BN", ascending=False)
        if total_active > 0 else pd.DataFrame(columns=["ward","Số BN"])
    )
    if total_active > 0:
        df_active = df_active.copy()
        df_active["days_in_hospital"] = df_active["admission_date"].apply(lambda d: days_between(d))
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
    }

def kpi(title: str, value: Any):
    st.markdown(f"""
        <div class="kpi">
            <h3>{title}</h3>
            <div class="v">{value}</div>
        </div>
    """, unsafe_allow_html=True)

def ward_bar_chart(df: pd.DataFrame):
    if df.empty:
        st.info("Chưa có dữ liệu BN theo phòng."); return
    chart = (
        alt.Chart(df.rename(columns={"ward":"Phòng"}))
        .mark_bar()
        .encode(x=alt.X("Số BN:Q"), y=alt.Y("Phòng:N", sort="-x"),
                tooltip=["Phòng:N","Số BN:Q"])
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)

def orders_status_chart(df_orders: pd.DataFrame):
    if df_orders.empty:
        st.info("Chưa có dữ liệu chỉ định."); return
    stat = df_orders.groupby("status").size().reset_index(name="Số lượng")
    stat.rename(columns={"status":"Trạng thái"}, inplace=True)
    chart = (
        alt.Chart(stat)
        .mark_bar()
        .encode(x=alt.X("Trạng thái:N", sort="-y"), y="Số lượng:Q",
                tooltip=["Trạng thái:N","Số lượng:Q"])
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)

# ======================
# Trang chủ
# ======================
if page == "Trang chủ":
    st.title("📊 Theo dõi bệnh nhân")

    df_all_wards = query_df("SELECT DISTINCT ward FROM patients WHERE ward IS NOT NULL AND ward<>'' ORDER BY ward")
    ward_list = ["Tất cả"] + (df_all_wards["ward"].tolist() if not df_all_wards.empty else [])
    f_col1, f_col2 = st.columns([1,2])
    with f_col1: ward_filter = st.selectbox("Lọc theo phòng", ward_list, index=0)
    with f_col2: st.markdown("<div class='small'>Gợi ý: dùng bộ lọc để xem nhanh khoa/phòng.</div>", unsafe_allow_html=True)

    stats = dashboard_stats({"ward": ward_filter})

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: kpi("BN đang điều trị", stats["total_active"])
    with c2: kpi("Thời gian điều trị TB (ngày)", stats["avg_days"])
    with c3: kpi("Chờ mổ", stats["count_wait_surg"])
    with c4: kpi("BN có order chưa xong", stats["pending_patients"])
    with c5: kpi("Order quá hạn / đến hạn", stats["scheduled_not_done"])
    st.markdown("---")

    st.subheader("BN theo phòng")
    ward_bar_chart(stats["patients_per_ward"])

    st.subheader("Trạng thái chỉ định")
    orders_status_chart(stats["df_orders"])

    with st.expander("📋 Danh sách BN (đang điều trị)", expanded=True):
        df_active = stats["df_active"]
        if df_active.empty:
            st.info("Không có bệnh nhân đang nằm.")
        else:
            base_cols = ["id","medical_id","name","ward","bed","surgery_needed","admission_date","diagnosis","notes","operated"]
            view_cols = [c for c in base_cols if c in df_active.columns]
            st.dataframe(
                df_active[view_cols].rename(columns={
                    "medical_id":"Mã BA","name":"Họ tên","ward":"Phòng","bed":"Giường",
                    "surgery_needed":"Cần mổ","admission_date":"Ngày NV",
                    "diagnosis":"Chẩn đoán","notes":"Ghi chú","operated":"Đã phẫu thuật"
                }), use_container_width=True, hide_index=True
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

    today = date.today()
    this_start, this_end = week_range(today, 0)
    last_start, last_end = week_range(today, -1)
    st.caption(f"Tuần này: **{this_start.strftime('%d/%m')} – {this_end.strftime('%d/%m/%Y')}**  •  Tuần trước: **{last_start.strftime('%d/%m')} – {last_end.strftime('%d/%m/%Y')}**")

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

    st.subheader("Ra viện vs Lượt điều trị (tuần này)")
    df1 = pd.DataFrame({"Chỉ số": ["Lượt điều trị", "Ra viện"], "Giá trị": [treatment_this, discharge_this]})
    st.altair_chart(alt.Chart(df1).mark_bar().encode(x="Chỉ số:N", y="Giá trị:Q", tooltip=["Chỉ số","Giá trị"]).properties(height=280), use_container_width=True)

    st.subheader("Chỉ định cận lâm sàng (tuần này) so với Lượt điều trị")
    df2 = pd.DataFrame({"Hạng mục": ["Chỉ định CLS", "Lượt điều trị"], "Số lượng": [orders_this, treatment_this]})
    st.altair_chart(alt.Chart(df2).mark_bar().encode(x="Hạng mục:N", y="Số lượng:Q", tooltip=["Hạng mục","Số lượng"]).properties(height=280), use_container_width=True)

    st.subheader("So sánh tuần này và tuần trước")
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

    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi("Lượt điều trị (tuần này)", treatment_this)
    with c2: kpi("Ra viện (tuần này)", discharge_this)
    with c3: kpi("CLS (tuần này)", orders_this)
    with c4: kpi("Số ngày điều trị TB/BN", avg_days_this)

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
            for r in df_room.to_dict(orient="records"):
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
        st.dataframe(
            df_show[["medical_id","name","ward","bed","admission_date","discharge_date","diagnosis","notes","Số ngày điều trị","surgery_needed","operated"]].rename(columns={
                "medical_id":"Mã BA","name":"Họ tên","ward":"Phòng","bed":"Giường",
                "admission_date":"Ngày NV","discharge_date":"Ngày XV",
                "diagnosis":"Chẩn đoán","notes":"Ghi chú","surgery_needed":"Cần mổ","operated":"Đã PT"
            }),
            use_container_width=True, hide_index=True
        )
        st.caption("Bấm ↩️ Quay lại để hủy xuất viện và chuyển BN về danh sách đang điều trị.")
        for r in df_src.to_dict(orient="records"):
            cols = st.columns([3,2,2,2,1])
            cols[0].markdown(f"**{r['name']}** — {r.get('medical_id') or '—'}")
            cols[1].markdown(f"Phòng {r.get('ward','')} • Giường {r.get('bed','') or '—'}")
            cols[2].markdown(f"NV: {r.get('admission_date','')} → XV: {r.get('discharge_date','')}")
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
# Nhập BN
# ======================
elif page == "Nhập BN":
    st.title("🧾 Nhập bệnh nhân mới")
    today_year = date.today().year

    with st.form("form_add_patient", clear_on_submit=True):
        c1, c2, c3 = st.columns([1,1,1])

        with c1:
            medical_id = st.text_input("Mã bệnh án (không bắt buộc)")
            ward = st.text_input("Phòng")
            bed = st.text_input("Giường")

        with c2:
            name = st.text_input("Họ tên *", value="")
            dob_year = st.number_input("Năm sinh (ưu tiên nhập năm)", min_value=1900, max_value=today_year, value=1980, step=1)
            st.caption(f"≈ Tuổi hiện tại: **{today_year - int(dob_year)}**")

        with c3:
            use_age = st.checkbox("Dùng tuổi để quy đổi năm sinh (tuỳ chọn)")
            dob_age = None
            if use_age:
                dob_age = st.number_input("Nhập tuổi hiện tại", min_value=0, max_value=130, value=45, step=1)
                st.caption(f"⇄ Quy đổi năm sinh: **{today_year - int(dob_age)}**")

        with st.expander("Nhập chi tiết ngày sinh (tuỳ chọn)"):
            use_detail = st.checkbox("Nhập chi tiết (ngày/tháng/năm)")
            dob_date = None
            if use_detail:
                try:
                    dob_date = st.date_input("Chọn ngày sinh chi tiết", value=date(1980,1,1), format="DD/MM/YYYY")
                except TypeError:
                    dob_date = st.date_input("Chọn ngày sinh chi tiết", value=date(1980,1,1))
                st.caption(f"Đã chọn: **{dob_date.strftime('%d/%m/%Y')}**")

        try:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today(), format="DD/MM/YYYY")
        except TypeError:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today())
            st.caption("Mẹo: Nhập theo dd/mm/yyyy. (Phiên bản Streamlit hiện tại không hỗ trợ format hiển thị)")

        planned_treatment_days = st.number_input("Thời gian điều trị dự kiến (ngày)", min_value=0, value=3)
        surgery_needed = st.checkbox("Cần phẫu thuật?")
        diagnosis = st.text_input("📝 Chẩn đoán bệnh", value="", placeholder="VD: Viêm phổi cộng đồng / ĐTĐ typ 2...")
        operated = st.checkbox("Đã phẫu thuật (nếu đã mổ)")

        meds = st.text_area("Thuốc chính")
        notes = st.text_area("Ghi chú")

        st.markdown("---")
        st.subheader("🧪 Chỉ định cận lâm sàng ban đầu (tùy chọn)")

        options = [f"{t[0]} — {t[1]}" for t in COMMON_TESTS]
        selected = st.multiselect("Chọn nhanh các chỉ định cần làm", options)
        try:
            scheduled_all = st.date_input("Ngày dự kiến thực hiện (áp dụng cho tất cả mục đã chọn)", value=date.today(), format="DD/MM/YYYY")
        except TypeError:
            scheduled_all = st.date_input("Ngày dự kiến thực hiện (áp dụng cho tất cả mục đã chọn)", value=date.today())

        submitted = st.form_submit_button("💾 Lưu bệnh nhân")
        if submitted:
            if use_detail and dob_date:
                dob_final = dob_date
            elif use_age and dob_age is not None:
                yr = max(1900, min(today_year, today_year - int(dob_age)))
                dob_final = date(yr, 1, 1)
            else:
                dob_final = date(int(dob_year), 1, 1)

            if not name.strip():
                st.error("Vui lòng nhập tối thiểu Họ tên.")
            else:
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
                    "notes": notes.strip(),
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
                    f"✅ Đã thêm BN • DOB: {dob_final.strftime('%d/%m/%Y')} • Nhập viện: {admission_date_ui.strftime('%d/%m/%Y')}"
                    + (f" • Đã tạo {len(selected)} chỉ định" if selected else "")
                )
                st.cache_data.clear()
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
    st.write("- Chạy: `streamlit run app.py --server.address 0.0.0.0 --server.port 8501`")
    st.write("- Bật mật khẩu: `export APP_PASSWORD=yourpass` / `set APP_PASSWORD=yourpass`")
    st.write("- File cơ sở dữ liệu:", DB_PATH)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Tạo dữ liệu mẫu (demo)"):
            load_sample_data()
            st.success("✅ Đã thêm sample data")
            st.cache_data.clear()
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
