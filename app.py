# app.py
# -*- coding: utf-8 -*-
import os
import sqlite3
from datetime import datetime, date, timedelta
from io import BytesIO
from typing import Dict, Any, Optional

import altair as alt
import pandas as pd
import streamlit as st

# ======================
# Cấu hình chung
# ======================
DB_PATH = "ward_tracker.db"
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
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ======================
# Helpers DB
# ======================
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
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
            active INTEGER DEFAULT 1
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
        conn.commit()

def _exec(query: str, params: tuple = ()) -> None:
    with get_conn() as conn:
        conn.execute(query, params)
        conn.commit()

def add_patient(patient: Dict[str, Any]) -> None:
    _exec("""
    INSERT INTO patients
    (medical_id, name, dob, ward, bed, admission_date, severity, surgery_needed,
     planned_treatment_days, meds, notes, active)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
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
    ))

def add_order(order: Dict[str, Any]) -> None:
    _exec("""
    INSERT INTO orders
    (patient_id, order_type, description, date_ordered, scheduled_date, status)
    VALUES (?,?,?,?,?,?)
    """, (order["patient_id"], order["order_type"], order.get("description", ""),
          order.get("date_ordered"), order.get("scheduled_date"), order.get("status", "pending")))

def mark_order_done(order_id: int, result_text: Optional[str] = None) -> None:
    now = date.today().strftime(DATE_FMT)
    _exec("UPDATE orders SET status='done', result=?, result_date=? WHERE id=?", (result_text, now, order_id))

def discharge_patient(patient_id: int) -> None:
    now = date.today().strftime(DATE_FMT)
    _exec("UPDATE patients SET discharge_date=?, active=0 WHERE id=?", (now, patient_id))

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
        st.experimental_rerun()

def load_sample_data():
    p1 = {"medical_id":"BN001","name":"Nguyễn A","dob":"1975-02-10","ward":"A1","bed":"01",
          "admission_date":date.today().strftime(DATE_FMT),"severity":4,"surgery_needed":1,
          "planned_treatment_days":7,"meds":"Thuốc A","notes":"Theo dõi huyết áp"}
    p2 = {"medical_id":"BN002","name":"Trần B","dob":"1965-06-15","ward":"A1","bed":"02",
          "admission_date":(date.today()-timedelta(days=2)).strftime(DATE_FMT),"severity":2,
          "surgery_needed":0,"planned_treatment_days":3,"meds":"Thuốc B","notes":""}
    p3 = {"medical_id":"BN003","name":"Lê C","dob":"1988-11-22","ward":"B2","bed":"05",
          "admission_date":(date.today()-timedelta(days=6)).strftime(DATE_FMT),"severity":5,
          "surgery_needed":1,"planned_treatment_days":10,"meds":"Thuốc C","notes":"Theo dõi sau mổ"}
    add_patient(p1); add_patient(p2); add_patient(p3)

    dfp = query_df("SELECT id, medical_id FROM patients")
    m = {r["medical_id"]: r["id"] for _, r in dfp.iterrows()}

    add_order({"patient_id":m["BN001"],"order_type":"CT","description":"CT não",
               "date_ordered":date.today().strftime(DATE_FMT),
               "scheduled_date":(date.today()+timedelta(days=1)).strftime(DATE_FMT),
               "status":"scheduled"})
    add_order({"patient_id":m["BN002"],"order_type":"XN máu","description":"Tổng phân tích",
               "date_ordered":(date.today()-timedelta(days=1)).strftime(DATE_FMT),
               "scheduled_date":date.today().strftime(DATE_FMT),
               "status":"pending"})
    add_order({"patient_id":m["BN003"],"order_type":"Siêu âm","description":"Ổ bụng",
               "date_ordered":date.today().strftime(DATE_FMT),
               "scheduled_date":(date.today()+timedelta(days=2)).strftime(DATE_FMT),
               "status":"scheduled"})

# ======================
# Tính toán Dashboard
# ======================
def dashboard_stats(filters: Dict[str, Any]) -> Dict[str, Any]:
    base_active = "SELECT * FROM patients WHERE active=1"
    params = []
    if filters.get("ward") and filters["ward"] != "Tất cả":
        base_active += " AND ward=?"; params.append(filters["ward"])
    if filters.get("sev_min", 1) > 1:
        base_active += " AND severity>=?"; params.append(filters["sev_min"])

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

    count_severe = int(query_df("SELECT COUNT(*) as c FROM patients WHERE active=1 AND severity>=4")["c"][0]) if total_active>=0 else 0
    count_wait_surg = int(query_df("SELECT COUNT(*) as c FROM patients WHERE active=1 AND surgery_needed=1")["c"][0]) if total_active>=0 else 0

    df_orders = query_df("""
        SELECT o.*, p.name, p.ward FROM orders o
        LEFT JOIN patients p ON o.patient_id=p.id
    """)
    pending_patients = df_orders[df_orders["status"]!="done"]["patient_id"].nunique() if not df_orders.empty else 0
    today = date.today().strftime(DATE_FMT)
    scheduled_not_done = 0
    if not df_orders.empty:
        mask = (df_orders["status"]!="done") & (df_orders["scheduled_date"].notna()) & (df_orders["scheduled_date"]<=today)
        scheduled_not_done = int(mask.sum())

    return {
        "total_active": total_active,
        "patients_per_ward": patients_per_ward,
        "avg_days": avg_days,
        "count_severe": count_severe,
        "count_wait_surg": count_wait_surg,
        "pending_patients": pending_patients,
        "scheduled_not_done": scheduled_not_done,
        "df_active": df_active,
        "df_orders": df_orders,
    }

# ======================
# Thành phần UI nhỏ (biểu đồ)
# ======================
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

def severity_chart(df_active: pd.DataFrame):
    if df_active.empty: return
    sev_df = df_active.groupby("severity").size().reset_index(name="Số BN")
    sev_df.rename(columns={"severity":"Mức độ"}, inplace=True)
    chart = (
        alt.Chart(sev_df)
        .mark_arc(innerRadius=40)
        .encode(theta="Số BN:Q", color="Mức độ:N",
                tooltip=["Mức độ:N","Số BN:Q"])
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
# Khởi tạo
# ======================
init_db()

# ======================
# Bảo vệ đơn giản bằng mật khẩu (tuỳ chọn)
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
page = st.sidebar.radio(
    "Chọn trang",
    ["Trang chủ", "Nhập BN", "Lịch XN/Chụp", "Tìm kiếm & Lịch sử", "Báo cáo", "Cài đặt / Demo"],
    index=0
)

# ======================
# Trang chủ
# ======================
if page == "Trang chủ":
    st.title("📊 Dashboard — Theo dõi bệnh nhân")

    df_all_wards = query_df("SELECT DISTINCT ward FROM patients WHERE ward IS NOT NULL AND ward<>'' ORDER BY ward")
    ward_list = ["Tất cả"] + (df_all_wards["ward"].tolist() if not df_all_wards.empty else [])
    f_col1, f_col2, f_col3 = st.columns([1,1,2])
    with f_col1: ward_filter = st.selectbox("Lọc theo phòng", ward_list, index=0)
    with f_col2: sev_min = st.slider("Mức độ nặng tối thiểu", 1, 5, 1)
    with f_col3: st.markdown("<div class='small'>Gợi ý: dùng bộ lọc để xem nhanh khoa/phòng hoặc nhóm BN nặng.</div>", unsafe_allow_html=True)

    stats = dashboard_stats({"ward": ward_filter, "sev_min": sev_min})

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1: kpi("BN đang điều trị", stats["total_active"])
    with c2: kpi("Thời gian điều trị TB (ngày)", stats["avg_days"])
    with c3: kpi("BN nặng (≥4)", stats["count_severe"])
    with c4: kpi("Chờ mổ", stats["count_wait_surg"])
    with c5: kpi("BN có order chưa xong", stats["pending_patients"])
    with c6: kpi("Order quá hạn / đến hạn", stats["scheduled_not_done"])
    st.markdown("---")

    g1, g2 = st.columns([2,1])
    with g1:
        st.subheader("BN theo phòng"); ward_bar_chart(stats["patients_per_ward"])
    with g2:
        st.subheader("Phân bố mức độ"); severity_chart(stats["df_active"])

    st.subheader("Trạng thái chỉ định"); orders_status_chart(stats["df_orders"])

    with st.expander("📋 Danh sách BN (đang điều trị)", expanded=True):
        df_active = stats["df_active"]
        if df_active.empty:
            st.info("Không có bệnh nhân đang nằm.")
        else:
            base_cols = ["id","medical_id","name","ward","bed","severity","surgery_needed","admission_date","notes"]
            view_cols = [c for c in base_cols if c in df_active.columns]
            st.dataframe(
                df_active[view_cols].rename(columns={
                    "medical_id":"Mã BA","name":"Họ tên","ward":"Phòng","bed":"Giường",
                    "severity":"Mức độ","surgery_needed":"Cần mổ","admission_date":"Ngày NV","notes":"Ghi chú"
                }), use_container_width=True, hide_index=True
            )
            for row in df_active.to_dict(orient="records"):
                cols = st.columns([1,3,1,1,1,1])
                cols[0].markdown(f"**{row['medical_id']}**")
                cols[1].markdown(f"**{row['name']}**  \n<span class='small'>{row.get('notes','')}</span>", unsafe_allow_html=True)
                cols[2].markdown(f"{row.get('ward','')}/{row.get('bed','') or ''}")
                sev_badge = "danger" if int(row.get("severity",0))>=4 else ("warn" if int(row.get("severity",0))==3 else "ok")
                cols[3].markdown(f"<span class='badge {sev_badge}'>Sev {row.get('severity')}</span>", unsafe_allow_html=True)
                cols[4].markdown("🔪 Cần mổ" if row.get("surgery_needed")==1 else "")
                if cols[5].button("Xuất viện", key=f"dis_{row['id']}"):
                    discharge_patient(row["id"]); st.success(f"Đã xuất viện {row['name']}"); safe_rerun()

# ======================
# Nhập BN
# ======================
elif page == "Nhập BN":
    st.title("🧾 Nhập bệnh nhân mới")
    today_year = date.today().year

    with st.form("form_add_patient", clear_on_submit=True):
        c1, c2, c3 = st.columns([1,1,1])

        with c1:
            medical_id = st.text_input("Mã bệnh án *")
            ward = st.text_input("Phòng")
            bed = st.text_input("Giường")

        with c2:
            name = st.text_input("Họ tên *", value="")
            # 1) Năm sinh (ưu tiên)
            dob_year = st.number_input(
                "Năm sinh (ưu tiên nhập năm)",
                min_value=1900, max_value=today_year, value=1980, step=1
            )
            st.caption(f"≈ Tuổi hiện tại: **{today_year - int(dob_year)}**")

        with c3:
            # 2) Tuỳ chọn quy đổi từ tuổi
            use_age = st.checkbox("Dùng tuổi để quy đổi năm sinh (tuỳ chọn)")
            dob_age = None
            if use_age:
                dob_age = st.number_input("Nhập tuổi hiện tại", min_value=0, max_value=130, value=45, step=1)
                st.caption(f"⇄ Quy đổi năm sinh: **{today_year - int(dob_age)}**")

        # 3) Tuỳ chọn nhập chi tiết ngày sinh
        with st.expander("Nhập chi tiết ngày sinh (tuỳ chọn)"):
            use_detail = st.checkbox("Nhập chi tiết (ngày/tháng/năm)")
            dob_date = None
            if use_detail:
                try:
                    dob_date = st.date_input("Chọn ngày sinh chi tiết", value=date(1980,1,1), format="DD/MM/YYYY")
                except TypeError:
                    dob_date = st.date_input("Chọn ngày sinh chi tiết", value=date(1980,1,1))
                st.caption(f"Đã chọn: **{dob_date.strftime('%d/%m/%Y')}**")

        # Ngày nhập viện: hiển thị dd/mm/yyyy + lịch popup
        try:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today(), format="DD/MM/YYYY")
        except TypeError:
            admission_date_ui = st.date_input("Ngày nhập viện", value=date.today())
            st.caption("Mẹo: Nhập theo dd/mm/yyyy. (Phiên bản Streamlit hiện tại không hỗ trợ format hiển thị)")

        severity = st.slider("Mức độ nặng (1 nhẹ → 5 nặng)", 1, 5, 2)
        planned_treatment_days = st.number_input("Thời gian điều trị dự kiến (ngày)", min_value=0, value=3)
        surgery_needed = st.checkbox("Cần phẫu thuật?")
        meds = st.text_area("Thuốc chính")
        notes = st.text_area("Ghi chú")

        submitted = st.form_submit_button("💾 Lưu bệnh nhân")
        if submitted:
            # Quy tắc chọn DOB theo mức ưu tiên:
            #   chi tiết > tuổi (nếu chọn) > năm sinh
            if use_detail and dob_date:
                dob_final = dob_date
            elif use_age and dob_age is not None:
                yr = max(1900, min(today_year, today_year - int(dob_age)))
                dob_final = date(yr, 1, 1)
            else:
                yr = int(dob_year)
                dob_final = date(yr, 1, 1)

            if not medical_id or not name:
                st.error("Vui lòng nhập tối thiểu Mã bệnh án và Họ tên.")
            else:
                patient = {
                    "medical_id": medical_id.strip(),
                    "name": name.strip(),
                    "dob": dob_final.strftime(DATE_FMT),              # lưu YYYY-MM-DD
                    "ward": ward.strip(),
                    "bed": bed.strip(),
                    "admission_date": admission_date_ui.strftime(DATE_FMT),  # lưu YYYY-MM-DD
                    "severity": int(severity),
                    "surgery_needed": surgery_needed,
                    "planned_treatment_days": int(planned_treatment_days),
                    "meds": meds.strip(),
                    "notes": notes.strip(),
                }
                add_patient(patient)
                st.success(
                    f"✅ Đã thêm bệnh nhân • DOB lưu: {dob_final.strftime('%d/%m/%Y')} • Ngày nhập viện: {admission_date_ui.strftime('%d/%m/%Y')}"
                )
                safe_rerun()

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
            order_type = st.selectbox("Loại", ["XN máu","X-quang","CT","Siêu âm","Khác"])
            desc = st.text_area("Mô tả")
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
                safe_rerun()
    else:
        st.info("Không có BN đang điều trị để thêm chỉ định.")

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
                st.write(f"Ngày NV: {r.get('admission_date','')} | Mức độ: {r.get('severity','')} | Phẫu thuật: {'Có' if r.get('surgery_needed',0)==1 else 'Không'} | Active: {r['active']}")
                st.write("Ghi chú:", r.get("notes",""))
                ords = query_df("SELECT * FROM orders WHERE patient_id=? ORDER BY scheduled_date DESC", (r["id"],))
                if not ords.empty:
                    st.table(ords[["order_type","description","scheduled_date","status","result_date"]])
                else:
                    st.write("Chưa có chỉ định.")
                if st.button("Xuất viện", key=f"dis2_{r['id']}"):
                    discharge_patient(r["id"])
                    st.success("✅ Đã xuất viện")
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
