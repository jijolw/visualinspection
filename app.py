"""
Streamlined Spring Management System - Complete Version
Simplified UI with unified dropdown+typing, full PDF generation, and spring-type analysis
"""

import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from typing import Dict, List, Optional
import json
import html

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

st.set_page_config(
    page_title="Spring Management System", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# -------------------------
# Supabase Configuration
# -------------------------
def get_supabase_credentials():
    try:
        supabase_url = st.secrets.get("SUPABASE_URL")
        supabase_key = st.secrets.get("SUPABASE_KEY")
        if supabase_url and supabase_key:
            return supabase_url, supabase_key
        
        supabase_url = st.secrets.get("supabase", {}).get("url")
        supabase_key = st.secrets.get("supabase", {}).get("anon_key")
        if supabase_url and supabase_key:
            return supabase_url, supabase_key
    except Exception:
        pass
    
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    
    try:
        with open(".streamlit/secrets.toml", "rb") as f:
            secrets = tomllib.load(f)
            supabase_url = secrets.get("SUPABASE_URL")
            supabase_key = secrets.get("SUPABASE_KEY")
            if supabase_url and supabase_key:
                return supabase_url, supabase_key
            
            supabase_url = secrets.get("supabase", {}).get("url")
            supabase_key = secrets.get("supabase", {}).get("anon_key")
            if supabase_url and supabase_key:
                return supabase_url, supabase_key
    except FileNotFoundError:
        pass
    
    raise ValueError("Supabase credentials not found!")

SUPABASE_URL, SUPABASE_KEY = get_supabase_credentials()

@st.cache_resource
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------
# Unified Dropdown + Typing Component
# -------------------------
def combobox(label, options, key, help_text=""):
    """Unified dropdown with ability to type new values"""
    col1, col2 = st.columns([4, 1])
    
    options_list = [""] + sorted(list(set(filter(None, options))))
    
    with col1:
        selected = st.selectbox(label, options_list, key=key, help=help_text)
    
    with col2:
        typed = st.text_input("", key=f"{key}_new", placeholder="new", label_visibility="collapsed")
    
    return typed if typed and typed.strip() else selected

# -------------------------
# Data Loading Functions
# -------------------------
@st.cache_data(ttl=60)
def _fetch_all_failures_cached():
    supabase = get_supabase_client()
    response = supabase.table('spring_failures').select('*').execute()
    return pd.DataFrame(response.data)

def fetch_all_failures(use_cache=True):
    if not use_cache:
        st.cache_data.clear()
        supabase = get_supabase_client()
        response = supabase.table('spring_failures').select('*').execute()
        return pd.DataFrame(response.data)
    return _fetch_all_failures_cached()

@st.cache_data(ttl=300)
def load_master_tables():
    supabase = get_supabase_client()
    try:
        spring_q = supabase.from_("spring_types").select("*").order("id").execute()
        defect_q = supabase.from_("defect_types").select("*").order("defect_code").execute()
        acts_q = supabase.from_("inspection_activities").select("*").order("sequence_number").execute()
        inspectors_q = supabase.from_("inspectors").select("*").eq("is_active", True).order("name").execute()

        spring_types = spring_q.data or []
        defect_types = defect_q.data or []
        acts = acts_q.data or []
        inspectors = inspectors_q.data or []
    except Exception as e:
        st.error(f"Error loading master tables: {e}")
        return [], [], [], [], []

    visual_acts = [a for a in acts if a.get("activity_type") == "VISUAL_INSPECTION" and a.get("is_active")]
    must_acts = [a for a in acts if a.get("activity_type") == "MUST_DO" and a.get("is_active")]
    return spring_types, defect_types, visual_acts, must_acts, inspectors

def get_unique_values(column):
    try:
        df = fetch_all_failures(use_cache=False)
        if df.empty or column not in df.columns:
            return []
        values = df[column].dropna().unique().tolist()
        return sorted([str(v) for v in values if v])
    except:
        return []

# -------------------------
# Business Logic
# -------------------------
def get_spring_counts(coach_type: str, secondary_type: str, spring_types: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    
    secondary_type_normalized = str(secondary_type).strip().upper() if secondary_type else ""
    
    for st_item in spring_types:
        coach_types = st_item.get("coach_types") or []
        if coach_type in coach_types:
            spring_name = str(st_item.get("spring_type", ""))
            
            if "AIR" in secondary_type_normalized and ("secondary" in spring_name.lower()):
                continue
            
            counts[spring_name] = int(st_item.get("max_per_bogie", 4))

    if "COIL" in secondary_type_normalized:
        for sec_name in ["Secondary Outer", "Secondary Inner"]:
            if sec_name not in counts:
                counts[sec_name] = 2

    return counts

def build_default_inspection_rows(activities: List[Dict], spring_counts: Dict[str, int], default_value: str):
    rows = []
    for act in activities:
        r = {"activity_id": act.get("id"), "activity": act.get("activity_text", ""), "remarks": ""}
        for stype in spring_counts.keys():
            key = stype.lower().replace(" ", "")
            r[key] = default_value
        rows.append(r)
    return rows

def normalize_sig_date(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    t = str(text).strip()
    if t == "":
        return None
    try:
        if len(t) == 10:
            d = datetime.fromisoformat(t).date()
            return d.isoformat()
        else:
            parsed = datetime.fromisoformat(t)
            return parsed.isoformat()
    except Exception:
        return t

# -------------------------
# PDF Generation
# -------------------------
def _para(text, style):
    if text is None:
        text = ""
    if isinstance(text, (list, dict)):
        text = json.dumps(text, ensure_ascii=False, indent=1)
    text = str(text)
    text = html.escape(text)
    text = text.replace("\n", "<br/>")
    return Paragraph(text, style)

def generate_inspection_pdf(record: Dict, defect_code_to_name: Dict, 
                           sig_shop_bytes: Optional[bytes] = None, 
                           sig_ins_bytes: Optional[bytes] = None) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), 
                          leftMargin=12*mm, rightMargin=12*mm, 
                          topMargin=12*mm, bottomMargin=12*mm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], 
                                alignment=TA_CENTER, fontSize=16, spaceAfter=6)
    h_style = ParagraphStyle("h", parent=styles["Heading3"], 
                           fontSize=11, spaceAfter=4)
    small = ParagraphStyle("small", parent=styles["Normal"], 
                         fontSize=8, leading=10, alignment=TA_LEFT)
    small.wordWrap = 'CJK'

    story = []
    story.append(Paragraph("SPRING INSPECTION REPORT", title_style))
    story.append(Spacer(1, 6))

    coach_info = [
        [_para("Coach Number:", small), _para(record.get("coach_number", ""), small), 
         _para("Coach Code:", small), _para(record.get("coach_code", ""), small)],
        [_para("Coach Type:", small), _para(record.get("coach_type", ""), small), 
         _para("Secondary Type:", small), _para(record.get("secondary_type", ""), small)],
        [_para("Bogie 1 No.:", small), _para(record.get("bogie1_number", ""), small), 
         _para("Bogie 2 No.:", small), _para(record.get("bogie2_number", ""), small)],
        [_para("Date of Receipt:", small), _para((record.get("date_of_receipt") or "")[:10], small), 
         _para("Inspector:", small), _para(str(record.get("inspector_name", "")), small)],
    ]
    
    ci_tbl = Table(coach_info, colWidths=[30*mm, 70*mm, 30*mm, 70*mm], hAlign="LEFT")
    ci_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f8e9")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(ci_tbl)
    story.append(Spacer(1, 8))

    spring_counts = record.get("spring_counts", {}) or {}
    if spring_counts:
        story.append(Paragraph("Spring Configuration", h_style))
        rows = [[_para("Spring Type", small), _para("Qty / Bogie", small)]]
        for k, v in spring_counts.items():
            rows.append([_para(k, small), _para(f"{v} per bogie", small)])
        t = Table(rows, colWidths=[110*mm, 30*mm], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976d2")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ]))
        story.append(t)
        story.append(Spacer(1, 8))

    b1 = record.get("bogie1_defects", []) or []
    b2 = record.get("bogie2_defects", []) or []
    bogie1_actual = record.get("bogie1_number", "Bogie 1")
    bogie2_actual = record.get("bogie2_number", "Bogie 2")

    story.append(Paragraph("Defects Summary", h_style))
    story.append(Paragraph(f"Bogie1: <b>{len(b1)}</b>  &nbsp;&nbsp; Bogie2: <b>{len(b2)}</b>  &nbsp;&nbsp; Total: <b>{len(b1)+len(b2)}</b>", small))
    story.append(Spacer(1, 6))

    defects_combined = []
    for d in b1:
        defect_code = d.get("defectType", "")
        defect_display = defect_code_to_name.get(defect_code, defect_code)
        defects_combined.append([
            _para(bogie1_actual, small),
            _para(d.get("springType", ""), small),
            _para(d.get("springNumber", ""), small),
            _para(defect_display, small),
            _para(d.get("location", ""), small)
        ])
    for d in b2:
        defect_code = d.get("defectType", "")
        defect_display = defect_code_to_name.get(defect_code, defect_code)
        defects_combined.append([
            _para(bogie2_actual, small),
            _para(d.get("springType", ""), small),
            _para(d.get("springNumber", ""), small),
            _para(defect_display, small),
            _para(d.get("location", ""), small)
        ])

    if defects_combined:
        hdr = [[_para("Bogie", small), _para("Spring Type", small), 
                _para("Spring No.", small), _para("Defect Type", small), 
                _para("Location", small)]] + defects_combined
        colw = [25*mm, 50*mm, 25*mm, 60*mm, 40*mm]
        tdef = Table(hdr, colWidths=colw, repeatRows=1, hAlign="LEFT")
        tdef.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c62828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tdef)
    else:
        story.append(Paragraph("<i>No defects reported.</i>", small))

    story.append(Spacer(1, 10))

    def render_inspection_table(title: str, activities: List[Dict], 
                               scounts: Dict[str, int], color_hex="#2e7d32"):
        story.append(Paragraph(title, h_style))
        cols = ["Activity"] + list(scounts.keys()) + ["Remarks"]
        total_w = 260 * mm
        act_w = 80 * mm
        rem_w = 40 * mm
        remaining = total_w - act_w - rem_w
        scnt = max(1, len(scounts))
        spring_w = remaining / scnt
        colw = [act_w] + [spring_w] * scnt + [rem_w]

        rows = [[_para(c, small) for c in cols]]
        for act in activities:
            row = [_para(act.get("activity", ""), small)]
            for stype in scounts.keys():
                key = stype.lower().replace(" ", "")
                row.append(_para(act.get(key, ""), small))
            row.append(_para(act.get("remarks", ""), small))
            rows.append(row)
        t = Table(rows, colWidths=colw, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(color_hex)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)
        story.append(Spacer(1, 8))

    render_inspection_table("Visual Inspection - Bogie 1", 
                          record.get("bogie1_inspections", []), spring_counts, "#2e7d32")
    render_inspection_table("Visual Inspection - Bogie 2", 
                          record.get("bogie2_inspections", []), spring_counts, "#2e7d32")
    render_inspection_table("Must Do - Bogie 1", 
                          record.get("bogie1_must_do", []), spring_counts, "#1565c0")
    render_inspection_table("Must Do - Bogie 2", 
                          record.get("bogie2_must_do", []), spring_counts, "#1565c0")

    story.append(Spacer(1, 12))
    story.append(Paragraph("Signatures", h_style))
    
    sig_table_data = [
        [_para("Prepared By (SSE SPRING SHOP)", small), _para("", small), 
         _para("Checked By (SSE / INSPECTION)", small), _para("", small)],
        [_para("Name & Signature:", small), 
         _para(record.get("_sig_shop_name", "") or "__________________", small), 
         _para("Name & Signature:", small), 
         _para(record.get("_sig_ins_name", "") or "__________________", small)],
        [_para("Date:", small), 
         _para(record.get("_sig_shop_date", "") or "__________________", small), 
         _para("Date:", small), 
         _para(record.get("_sig_ins_date", "") or "__________________", small)],
    ]
    
    sig_tbl = Table(sig_table_data, colWidths=[50*mm, 70*mm, 50*mm, 70*mm], hAlign="LEFT")
    sig_tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), 
                                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(sig_tbl)

    if sig_shop_bytes or sig_ins_bytes:
        img_cells = [None, None]
        try:
            if sig_shop_bytes:
                img_cells[0] = RLImage(BytesIO(sig_shop_bytes), width=45*mm, height=20*mm)
            if sig_ins_bytes:
                img_cells[1] = RLImage(BytesIO(sig_ins_bytes), width=45*mm, height=20*mm)

            img_row = [
                img_cells[0] if img_cells[0] is not None else _para("", small),
                _para("", small),
                img_cells[1] if img_cells[1] is not None else _para("", small),
                _para("", small),
            ]
            img_tbl = Table([img_row], colWidths=[50*mm, 70*mm, 50*mm, 70*mm], hAlign="LEFT")
            story.append(Spacer(1, 6))
            story.append(img_tbl)
        except Exception:
            pass

    doc.build(story)
    buf.seek(0)
    return buf.read()

# -------------------------
# Load Master Data
# -------------------------
spring_types, defect_types, visual_activities, mustdo_activities, inspectors = load_master_tables()

defect_code_list = [d.get("defect_code") for d in defect_types] if defect_types else []
defect_code_to_name = {d.get("defect_code"): d.get("defect_name") for d in defect_types} if defect_types else {}

VISUAL_OPTIONS = ["Satisfactory", "Unsatisfactory"]
MUSTDO_OPTIONS = ["Done", "Not Done"]

# -------------------------
# Session State Initialization
# -------------------------
if "last_saved_pdf" not in st.session_state:
    st.session_state["last_saved_pdf"] = None
if "last_saved_pdf_name" not in st.session_state:
    st.session_state["last_saved_pdf_name"] = None

# -------------------------
# UI Navigation
# -------------------------
st.sidebar.title("Spring Management System")
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Add Failure", "View Failures", "Generate Report"]
)

# =============================
# PAGE: Dashboard - Enhanced Analysis
# =============================
if page == "Dashboard":
    st.title("Spring Failure Analysis Dashboard")
    
    df = fetch_all_failures()
    
    if df.empty:
        st.warning("No failure data available yet.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Failures", len(df))
        with col2:
            st.metric("Unique Coach Codes", df['coach_code'].nunique())
        with col3:
            st.metric("Failure Types", df['type_of_failure'].nunique())
        with col4:
            st.metric("Spring Types", df['type_of_spring'].nunique())
        
        st.divider()
        
        st.subheader("Top 10 Defect Types")
        defect_counts = df['type_of_failure'].value_counts().head(10).sort_values(ascending=True)
        fig = px.bar(y=defect_counts.index, x=defect_counts.values,
                    orientation='h', labels={'x': 'Count', 'y': 'Defect Type'},
                    color=defect_counts.values, color_continuous_scale='Reds',
                    text=defect_counts.values)
        fig.update_traces(textposition='auto')
        st.plotly_chart(fig, use_container_width=True)
        
        st.divider()
        
        st.subheader("Failure Analysis by Spring Type & Coach Type")
        
        tab1, tab2, tab3, tab4 = st.tabs([
            "Spring Type Overview", 
            "Coach Type vs Defect", 
            "Spring Type vs Defect",
            "Defect by Coach Type"
        ])
        
        with tab1:
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Failures by Spring Type**")
                spring_counts = df['type_of_spring'].value_counts()
                fig = px.bar(x=spring_counts.index, y=spring_counts.values,
                            labels={'x': 'Spring Type', 'y': 'Count'},
                            color=spring_counts.values, color_continuous_scale='Blues',
                            text=spring_counts.values)
                fig.update_traces(textposition='auto')
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.write("**Failures by Coach Type**")
                coach_type_counts = df['coach_type'].value_counts()
                fig = px.pie(values=coach_type_counts.values, names=coach_type_counts.index, hole=0.3)
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
        
        with tab2:
            st.write("**Coach Type vs Defect Type (Heatmap)**")
            cross_tab = pd.crosstab(df['coach_type'], df['type_of_failure'])
            
            fig = px.imshow(cross_tab, 
                           labels=dict(x="Defect Type", y="Coach Type", color="Count"),
                           color_continuous_scale='YlOrRd', aspect="auto")
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)
            
            st.write("**Detailed Cross-tabulation:**")
            st.dataframe(cross_tab, use_container_width=True)
        
        with tab3:
            st.write("**Spring Type vs Defect Type (Heatmap)**")
            cross_tab2 = pd.crosstab(df['type_of_spring'], df['type_of_failure'])
            
            fig = px.imshow(cross_tab2,
                           labels=dict(x="Defect Type", y="Spring Type", color="Count"),
                           color_continuous_scale='YlOrRd', aspect="auto")
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)
            
            st.write("**Detailed Cross-tabulation:**")
            st.dataframe(cross_tab2, use_container_width=True)
        
        with tab4:
            st.write("**Defect Distribution by Coach Type**")
            
            for coach_type in sorted(df['coach_type'].unique()):
                if pd.notna(coach_type):
                    st.write(f"**{coach_type} Coaches**")
                    coach_data = df[df['coach_type'] == coach_type]
                    defect_dist = coach_data['type_of_failure'].value_counts()
                    
                    fig = px.bar(x=defect_dist.index, y=defect_dist.values,
                               labels={'x': 'Defect Type', 'y': 'Count'},
                               text=defect_dist.values)
                    fig.update_traces(textposition='auto')
                    fig.update_layout(height=300)
                    st.plotly_chart(fig, use_container_width=True)
        
        st.divider()
        
        st.subheader("Spring Colour & Suspension Analysis")
        col1, col2 = st.columns(2)
        
        with col1:
            colour_counts = df['colour_of_spring'].value_counts()
            fig = px.bar(x=colour_counts.index, y=colour_counts.values,
                        labels={'x': 'Colour', 'y': 'Count'},
                        color=colour_counts.values, color_continuous_scale='Viridis',
                        text=colour_counts.values)
            fig.update_traces(textposition='auto')
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            suspension_counts = df['secondary_suspension_type'].value_counts()
            fig = px.bar(x=suspension_counts.index, y=suspension_counts.values,
                        labels={'x': 'Suspension Type', 'y': 'Count'},
                        color=suspension_counts.values, color_continuous_scale='Blues',
                        text=suspension_counts.values)
            fig.update_traces(textposition='auto')
            st.plotly_chart(fig, use_container_width=True)

# =============================
# PAGE: Add Failure - SIMPLIFIED
# =============================
elif page == "Add Failure":
    st.title("Add Spring Failure Record")
    st.info("Use dropdowns to select existing values or type new ones in the right column. Fields marked with * are required.")
    
    with st.form("new_failure_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        
        existing_coach_nos = get_unique_values('coach_no')
        existing_coach_codes = get_unique_values('coach_code')
        existing_schedules = get_unique_values('schedule')
        existing_divisions = get_unique_values('division')
        existing_bogie_numbers = ["1", "2", "3", "4"]
        existing_suspension_types = get_unique_values('secondary_suspension_type')
        existing_spring_types = get_unique_values('type_of_spring')
        existing_spring_colours = get_unique_values('colour_of_spring')
        existing_failure_types = get_unique_values('type_of_failure')
        existing_locations = get_unique_values('location')
        existing_locations_in_bogie = get_unique_values('location_in_bogie')
        
        with col1:
            st.write("**Basic Info**")
            coach_no = st.text_input("Coach No *", placeholder="e.g., 45001")
            
            coach_type = st.selectbox("Coach Type *", ["VB", "LHB"])
            
            coach_code = combobox("Coach Code", existing_coach_codes, key="coach_code")
            
            bogie_number = st.text_input("Bogie No", placeholder="e.g., 1, 2, 3, 4")
            
            schedule = combobox("Schedule", existing_schedules, key="schedule")
        
        with col2:
            st.write("**Spring Details**")
            receipt_date = st.date_input("Receipt Date")
            
            type_of_spring = combobox("Spring Type", existing_spring_types, key="type_of_spring")
            
            colour_of_spring = combobox("Spring Colour", existing_spring_colours, key="colour_of_spring")
            
            secondary_suspension_type = combobox("Secondary Suspension", existing_suspension_types, 
                                               key="secondary_suspension")
            
            division = combobox("Division", existing_divisions, key="division")
        
        with col3:
            st.write("**Defect Info**")
            type_of_failure = combobox("Defect Type", existing_failure_types, key="type_of_failure")
            
            location = combobox("Location on Spring", existing_locations, key="location")
            
            location_in_bogie = combobox("Location in Bogie", existing_locations_in_bogie, 
                                        key="location_in_bogie")
            
            defect_count = st.number_input("Count (same defect in same bogie)", min_value=1, value=1, 
                                          help="If same defect repeats in same bogie, enter count instead of multiple entries")
            
            mfg = st.text_input("MFG", placeholder="Optional")
        
        st.write("**Additional**")
        remarks = st.text_area("Remarks", height=80, placeholder="Optional notes")
        
        submitted = st.form_submit_button("Add Record", use_container_width=True, type="primary")
        
        if submitted:
            if not coach_no or not coach_no.strip():
                st.error("Coach No is required!")
            elif not coach_type:
                st.error("Coach Type is required!")
            else:
                try:
                    supabase = get_supabase_client()
                    new_record = {
                        "coach_no": coach_no.strip(),
                        "coach_code": coach_code.strip() if coach_code else None,
                        "coach_type": coach_type,
                        "schedule": schedule.strip() if schedule else None,
                        "division": division.strip() if division else None,
                        "bogie_number": bogie_number.strip() if bogie_number else None,
                        "receipt_date": receipt_date.isoformat(),
                        "secondary_suspension_type": secondary_suspension_type.strip() if secondary_suspension_type else None,
                        "type_of_spring": type_of_spring.strip() if type_of_spring else None,
                        "colour_of_spring": colour_of_spring.strip() if colour_of_spring else None,
                        "type_of_failure": type_of_failure.strip() if type_of_failure else None,
                        "location": location.strip() if location else None,
                        "location_in_bogie": location_in_bogie.strip() if location_in_bogie else None,
                        "remarks": remarks.strip() if remarks else None,
                        "mfg": mfg.strip() if mfg else None,
                        "defect_count": int(defect_count)
                    }
                    
                    response = supabase.table('spring_failures').insert(new_record).execute()
                    st.success("Record added successfully!")
                    st.cache_data.clear()
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Error adding record: {str(e)}")

# =============================
# PAGE: View Failures
# =============================
elif page == "View Failures":
    st.title("View & Manage Failure Records")
    
    df = fetch_all_failures()
    
    if df.empty:
        st.warning("No records found.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            coach_filter = st.multiselect("Coach No", sorted(df['coach_no'].unique()))
        with col2:
            defect_filter = st.multiselect("Defect Type", df['type_of_failure'].unique())
        with col3:
            spring_filter = st.multiselect("Spring Type", df['type_of_spring'].unique())
        with col4:
            coach_type_filter = st.multiselect("Coach Type", df['coach_type'].unique())
        
        filtered_df = df.copy()
        if coach_filter:
            filtered_df = filtered_df[filtered_df['coach_no'].isin(coach_filter)]
        if defect_filter:
            filtered_df = filtered_df[filtered_df['type_of_failure'].isin(defect_filter)]
        if spring_filter:
            filtered_df = filtered_df[filtered_df['type_of_spring'].isin(spring_filter)]
        if coach_type_filter:
            filtered_df = filtered_df[filtered_df['coach_type'].isin(coach_type_filter)]
        
        st.write(f"Showing {len(filtered_df)} of {len(df)} records")
        
        display_df = filtered_df.drop(['created_at', 'updated_at'], axis=1, errors='ignore').reset_index(drop=True)
        st.dataframe(display_df, use_container_width=True)
        
        st.divider()
        
        col1, col2 = st.columns([3, 1])
        with col1:
            row_index = st.number_input("Select row to edit/delete:", 
                                       min_value=0, 
                                       max_value=len(display_df)-1 if len(display_df) > 0 else 0)
        
        if len(display_df) > 0:
            selected_record = filtered_df.iloc[row_index]
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Delete Record", key=f"delete_{row_index}", use_container_width=True):
                    supabase = get_supabase_client()
                    supabase.table('spring_failures').delete().eq('id', selected_record['id']).execute()
                    st.success("Record deleted!")
                    st.cache_data.clear()
                    st.rerun()
            
            with col2:
                if st.button("Edit Record", key=f"edit_{row_index}", use_container_width=True):
                    st.session_state.editing_id = selected_record['id']
        
        if 'editing_id' in st.session_state:
            st.divider()
            st.subheader("Edit Record")
            
            record_to_edit = df[df['id'] == st.session_state.editing_id].iloc[0]
            
            with st.form("edit_record_form"):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    coach_no_edit = st.text_input("Coach No", value=record_to_edit['coach_no'])
                    coach_type_edit = st.selectbox("Coach Type", ["VB", "LHB"], 
                                                  index=0 if record_to_edit['coach_type'] == "VB" else 1)
                    bogie_number_edit = st.text_input("Bogie Number", value=str(record_to_edit.get('bogie_number', '')))
                
                with col2:
                    type_of_spring_edit = st.text_input("Spring Type", value=str(record_to_edit['type_of_spring']))
                    colour_of_spring_edit = st.text_input("Spring Colour", value=str(record_to_edit['colour_of_spring']))
                    secondary_suspension_edit = st.text_input("Secondary Suspension", 
                                                             value=str(record_to_edit['secondary_suspension_type']))
                
                with col3:
                    type_of_failure_edit = st.text_input("Defect Type", value=str(record_to_edit['type_of_failure']))
                    location_edit = st.text_input("Location", value=str(record_to_edit.get('location', '')))
                    location_in_bogie_edit = st.text_input("Location in Bogie", 
                                                          value=str(record_to_edit.get('location_in_bogie', '')))
                
                remarks_edit = st.text_area("Remarks", value=str(record_to_edit.get('remarks', '') or ''))
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.form_submit_button("Save", use_container_width=True):
                        supabase = get_supabase_client()
                        updated_data = {
                            'coach_no': coach_no_edit,
                            'coach_type': coach_type_edit,
                            'bogie_number': bogie_number_edit if bogie_number_edit else None,
                            'type_of_spring': type_of_spring_edit,
                            'colour_of_spring': colour_of_spring_edit,
                            'secondary_suspension_type': secondary_suspension_edit,
                            'type_of_failure': type_of_failure_edit,
                            'location': location_edit if location_edit else None,
                            'location_in_bogie': location_in_bogie_edit if location_in_bogie_edit else None,
                            'remarks': remarks_edit if remarks_edit else None
                        }
                        supabase.table('spring_failures').update(updated_data).eq('id', st.session_state.editing_id).execute()
                        st.success("Record updated!")
                        del st.session_state.editing_id
                        st.cache_data.clear()
                        st.rerun()
                
                with col2:
                    if st.form_submit_button("Cancel", use_container_width=True):
                        del st.session_state.editing_id
                        st.rerun()
        
        st.divider()
        csv = display_df.to_csv(index=False)
        st.download_button(
            label="Download as CSV",
            data=csv,
            file_name=f"spring_failures_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )

# =============================
# PAGE: Generate Report
# =============================
elif page == "Generate Report":
    st.title("Generate Inspection Report")
    st.info("Select a coach to generate a comprehensive PDF inspection report with all defects and inspection activities.")
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.write("")
    with col2:
        if st.button("Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    
    df = fetch_all_failures(use_cache=False)
    
    if df.empty:
        st.warning("No failure records found. Please add failures first.")
    else:
        unique_coaches = sorted(df['coach_no'].unique())
        
        coach_number = st.selectbox("Select Coach Number", options=unique_coaches)
        
        if coach_number:
            coach_failures = df[df['coach_no'] == coach_number].copy()
            
            st.success(f"Found {len(coach_failures)} defect(s) for Coach {coach_number}")
            
            first_record = coach_failures.iloc[0]
            coach_code = first_record.get('coach_code', '')
            
            existing_coach_type = first_record.get('coach_type', None)
            if not existing_coach_type or pd.isna(existing_coach_type):
                coach_code_str = str(coach_code).upper().strip()
                if 'VB' in coach_code_str:
                    existing_coach_type = 'VB'
                elif 'LHB' in coach_code_str or 'LW' in coach_code_str:
                    existing_coach_type = 'LHB'
                else:
                    existing_coach_type = 'LHB'
            
            existing_secondary_type = first_record.get('secondary_suspension_type', 'Air Spring')
            if not existing_secondary_type or pd.isna(existing_secondary_type):
                existing_secondary_type = 'Air Spring'
            
            existing_bogie1 = first_record.get('bogie_number', '')
            existing_bogie2 = ""
                
            existing_receipt_date = pd.to_datetime(first_record.get('receipt_date', datetime.now())).date()
            
            with st.expander("View Existing Defects", expanded=True):
                display_cols = ['bogie_number', 'type_of_spring', 'colour_of_spring', 
                              'type_of_failure', 'location', 'location_in_bogie']
                
                st.write("**Update Bogie Numbers if needed:**")
                
                col1, col2, col3 = st.columns([2, 2, 2])
                with col1:
                    st.write("**Original Bogie**")
                    for idx, val in enumerate(coach_failures['bogie_number']):
                        st.write(f"Row {idx + 1}: {val if val else '(empty)'}")
                
                with col2:
                    st.write("**Correct Bogie (if needed)**")
                    bogie_corrections = {}
                    for idx in range(len(coach_failures)):
                        corrected = st.text_input(f"Correct bogie for row {idx + 1}:", 
                                                 value="", 
                                                 placeholder=str(coach_failures.iloc[idx]['bogie_number']) if coach_failures.iloc[idx]['bogie_number'] else "e.g., 1 or 2",
                                                 key=f"bogie_correct_{idx}")
                        if corrected:
                            bogie_corrections[idx] = corrected
                
                with col3:
                    st.write("**Defect Details**")
                    for idx, row in coach_failures.iterrows():
                        details = f"{row.get('type_of_spring', '')} - {row.get('type_of_failure', '')}"
                        st.write(f"Row {idx + 1}: {details}")
                
                for idx, corrected_bogie in bogie_corrections.items():
                    coach_failures.iloc[idx, coach_failures.columns.get_loc('bogie_number')] = corrected_bogie
                
                st.success("Bogie numbers updated for this session")
                st.dataframe(coach_failures[display_cols], use_container_width=True)
            
            st.markdown("---")
            st.markdown("### Complete Report Details")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write(f"**Coach Number:** {coach_number}")
                st.write(f"**Coach Code:** {coach_code}")
                st.write(f"**Coach Type:** {existing_coach_type}")
            
            with col2:
                st.write(f"**Secondary Type:** {existing_secondary_type}")
                st.write(f"**Date of Receipt:** {existing_receipt_date}")
                st.write(f"**Bogie from Defect Record:** {existing_bogie1 if existing_bogie1 else '(not specified)'}")
            
            st.markdown("---")
            st.markdown("### Enter Bogie Numbers for Report")
            
            col1, col2 = st.columns(2)
            with col1:
                report_bogie1 = st.text_input("Bogie 1 Number", placeholder="e.g., 1, 2, A1, B2", 
                                             value="", help="Enter bogie number for first bogie")
            with col2:
                report_bogie2 = st.text_input("Bogie 2 Number", placeholder="e.g., 1, 2, A1, B2", 
                                             value="", help="Leave blank if only one bogie or enter second bogie number")
            
            spring_counts = get_spring_counts(existing_coach_type, existing_secondary_type, spring_types)
            st.info(f"**Spring Configuration:** {', '.join([f'{k} ({v}/bogie)' for k, v in spring_counts.items()])}")
            
            st.session_state['current_spring_counts'] = spring_counts
            st.session_state['current_coach_type'] = existing_coach_type
            st.session_state['current_secondary_type'] = existing_secondary_type
            
            inspector_options = [None] + [ins.get("id") for ins in inspectors]
            inspector_names = {None: "Select Inspector"} | {ins["id"]: ins["name"] for ins in inspectors}
            inspector_id = st.selectbox(
                "Inspector",
                options=inspector_options,
                format_func=lambda x: inspector_names.get(x, "")
            )
            
            st.markdown("---")
            st.markdown("### Signatures")
            col1, col2 = st.columns(2)
            
            with col1:
                sig_shop_name = st.text_input("SSE SPRING SHOP - Name")
                sig_shop_file = st.file_uploader("SSE SPRING SHOP - Signature", type=["png", "jpg", "jpeg"])
                sig_shop_date = st.text_input("Signature Date (YYYY-MM-DD)", key="sig_shop_date_report")
            
            with col2:
                sig_ins_name = st.text_input("SSE / INSPECTION - Name")
                sig_ins_file = st.file_uploader("SSE / INSPECTION - Signature", type=["png", "jpg", "jpeg"])
                sig_ins_date = st.text_input("Signature Date (YYYY-MM-DD)", key="sig_ins_date_report")
            
            st.markdown("### Visual Inspection Activities")
            
            default_vis_b1 = build_default_inspection_rows(visual_activities, spring_counts, "Satisfactory")
            default_vis_b2 = build_default_inspection_rows(visual_activities, spring_counts, "Satisfactory")
            
            def df_with_categories(rows, is_visual=True):
                df_temp = pd.DataFrame(rows)
                categories = VISUAL_OPTIONS if is_visual else MUSTDO_OPTIONS
                for stype in spring_counts.keys():
                    key = stype.lower().replace(" ", "")
                    if key in df_temp.columns:
                        df_temp[key] = pd.Categorical(df_temp[key].astype(str).fillna(""), 
                                                     categories=categories + [""])
                return df_temp
            
            st.subheader("Visual Inspection — Bogie 1")
            edited_vis_b1 = st.data_editor(df_with_categories(default_vis_b1, True), 
                                          num_rows="dynamic", use_container_width=True, 
                                          key="vis_b1_report", hide_index=True)
            
            st.subheader("Visual Inspection — Bogie 2")
            edited_vis_b2 = st.data_editor(df_with_categories(default_vis_b2, True), 
                                          num_rows="dynamic", use_container_width=True, 
                                          key="vis_b2_report", hide_index=True)
            
            st.markdown("### Must Do Activities")
            
            default_must_b1 = build_default_inspection_rows(mustdo_activities, spring_counts, "Done")
            default_must_b2 = build_default_inspection_rows(mustdo_activities, spring_counts, "Done")
            
            st.subheader("Must Do — Bogie 1")
            edited_must_b1 = st.data_editor(df_with_categories(default_must_b1, False), 
                                           num_rows="dynamic", use_container_width=True, 
                                           key="must_b1_report", hide_index=True)
            
            st.subheader("Must Do — Bogie 2")
            edited_must_b2 = st.data_editor(df_with_categories(default_must_b2, False), 
                                           num_rows="dynamic", use_container_width=True, 
                                           key="must_b2_report", hide_index=True)
            
            st.markdown("---")
            
            if st.button("Generate PDF Report", type="primary", use_container_width=True):
                bogie1_defects = []
                bogie2_defects = []
                
                for _, row in coach_failures.iterrows():
                    defect = {
                        "springType": row['type_of_spring'],
                        "springNumber": str(row.get('location_in_bogie', '')),
                        "defectType": row['type_of_failure'],
                        "location": row['location']
                    }
                    
                    bogie_num = str(row.get('bogie_number', '1')).strip()
                    if bogie_num == '2':
                        bogie2_defects.append(defect)
                    else:
                        bogie1_defects.append(defect)
                
                def clean_inspections(editor_df, default_val):
                    if editor_df is None:
                        return []
                    df_temp = editor_df.copy().fillna("")
                    recs = df_temp.to_dict(orient="records")
                    for r in recs:
                        for k, v in list(r.items()):
                            if v == "" and k not in ("activity", "remarks", "activity_id"):
                                r[k] = default_val
                    return recs
                
                bogie1_inspections = clean_inspections(edited_vis_b1, "Satisfactory")
                bogie2_inspections = clean_inspections(edited_vis_b2, "Satisfactory")
                bogie1_must = clean_inspections(edited_must_b1, "Done")
                bogie2_must = clean_inspections(edited_must_b2, "Done")
                
                sig_shop_bytes = sig_shop_file.read() if sig_shop_file is not None else None
                sig_ins_bytes = sig_ins_file.read() if sig_ins_file is not None else None
                
                _sig_shop_date_val = normalize_sig_date(sig_shop_date)
                _sig_ins_date_val = normalize_sig_date(sig_ins_date)
                
                inspector_name = ""
                if inspector_id:
                    inspector_name = next((i["name"] for i in inspectors if i["id"] == inspector_id), "")
                
                record = {
                    "coach_number": coach_number,
                    "coach_code": coach_code,
                    "coach_type": existing_coach_type,
                    "secondary_type": existing_secondary_type,
                    "bogie1_number": report_bogie1 if report_bogie1 else "Bogie 1",
                    "bogie2_number": report_bogie2 if report_bogie2 else "",
                    "date_of_receipt": datetime.combine(existing_receipt_date, datetime.min.time()).isoformat(),
                    "spring_counts": spring_counts,
                    "bogie1_inspections": bogie1_inspections,
                    "bogie2_inspections": bogie2_inspections,
                    "bogie1_must_do": bogie1_must,
                    "bogie2_must_do": bogie2_must,
                    "bogie1_defects": bogie1_defects,
                    "bogie2_defects": bogie2_defects,
                    "inspector_id": inspector_id,
                    "inspector_name": inspector_name,
                    "_sig_shop_name": sig_shop_name or None,
                    "_sig_ins_name": sig_ins_name or None,
                    "_sig_shop_date": _sig_shop_date_val,
                    "_sig_ins_date": _sig_ins_date_val,
                }
                
                pdf_bytes = generate_inspection_pdf(record, defect_code_to_name, 
                                                   sig_shop_bytes=sig_shop_bytes, 
                                                   sig_ins_bytes=sig_ins_bytes)
                
                st.session_state["last_saved_pdf"] = pdf_bytes
                st.session_state["last_saved_pdf_name"] = f"inspection_{coach_code}_{coach_number}.pdf"
                
                st.success(f"Report generated for Coach {coach_number} with {len(bogie1_defects) + len(bogie2_defects)} defects!")
    
    if st.session_state.get("last_saved_pdf"):
        st.markdown("---")
        st.success("PDF Report Ready!")
        st.download_button(
            "Download Inspection Report", 
            st.session_state["last_saved_pdf"], 
            file_name=st.session_state.get("last_saved_pdf_name", "inspection_report.pdf"), 
            mime="application/pdf",
            use_container_width=True
        )
        if st.button("Clear PDF", use_container_width=True):
            st.session_state["last_saved_pdf"] = None
            st.session_state["last_saved_pdf_name"] = None
            st.rerun()