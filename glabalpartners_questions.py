# globalpartners.py
# Full Streamlit dashboard (single file) implementing:
# - CLV (High / Mid / Low) with filter
# - RFM (VIP / New Customer / Churn Risk) with filter
# - Activity / At-Risk filter (>45 days)
# - Time summaries (daily/weekly/monthly), by location & category
# - Loyalty impact table
# - Location performance table
# - Discount effectiveness table
#
# Assumptions:
# - CSVs present in working dir: order_items.csv, order_item_options.csv, date_dim.csv
# - Uses "restaurant_id" as location
#
# Run:
# streamlit run globalpartners.py

import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Global Partners Analytics", layout="wide")

# ---------------------------
# Helper: fill NaNs (Option C)
# Replace NaNs immediately after loading each CSV as requested
# ---------------------------
def fillna_entire_df(df):
    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_bool_dtype(dtype):
            df[col] = df[col].fillna(False)
        elif pd.api.types.is_numeric_dtype(dtype):
            df[col] = df[col].fillna(0)
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            # fill with epoch UTC
            df[col] = df[col].fillna(pd.Timestamp("1970-01-01", tz="UTC"))
        else:
            df[col] = df[col].fillna("")  # object/string
    return df

# ---------------------------
# Load and prepare raw CSVs
# ---------------------------
@st.cache_data
def load_and_prepare():
    # read CSVs
    oi = pd.read_csv("order_items.csv", low_memory=False)
    oio = pd.read_csv("order_item_options.csv", low_memory=False)
    dd = pd.read_csv("date_dim.csv", low_memory=False)

    # normalize column names to lower
    oi.columns = [c.strip().lower() for c in oi.columns]
    oio.columns = [c.strip().lower() for c in oio.columns]
    dd.columns = [c.strip().lower() for c in dd.columns]

    # Immediately replace NaNs in each dataframe (Option C)
    oi = fillna_entire_df(oi)
    oio = fillna_entire_df(oio)
    dd = fillna_entire_df(dd)

    # Parse creation_time_utc robustly; coerce errors to NaT then fill (we already filled NaNs)
    if "creation_time_utc" in oi.columns:
        oi["creation_time_utc"] = pd.to_datetime(oi["creation_time_utc"], errors="coerce", utc=True)
        oi["creation_time_utc"] = oi["creation_time_utc"].fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    else:
        # fallback: pick any date-like column if present
        candidates = [c for c in oi.columns if "time" in c or "date" in c]
        if candidates:
            oi["creation_time_utc"] = pd.to_datetime(oi[candidates[0]], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
        else:
            oi["creation_time_utc"] = pd.Timestamp("1970-01-01", tz="UTC")

    # Ensure numeric columns
    oi["item_price"] = pd.to_numeric(oi.get("item_price", 0), errors="coerce").fillna(0.0)
    oi["item_quantity"] = pd.to_numeric(oi.get("item_quantity", 0), errors="coerce").fillna(0).astype(int)

    oio["option_price"] = pd.to_numeric(oio.get("option_price", 0), errors="coerce").fillna(0.0)
    oio["option_quantity"] = pd.to_numeric(oio.get("option_quantity", 0), errors="coerce").fillna(0).astype(int)

    # Convert ID columns to strings (avoid Arrow conversion issues)
    for c in ["user_id", "order_id", "lineitem_id", "restaurant_id"]:
        if c in oi.columns:
            oi[c] = oi[c].astype(str).fillna("")
        else:
            oi[c] = ""

    for c in ["order_id", "lineitem_id"] :
        if c in oio.columns:
            oio[c] = oio[c].astype(str).fillna("")
        else:
            oio[c] = ""

    # Normalize is_loyalty to boolean
    if "is_loyalty" in oi.columns:
        oi["is_loyalty"] = oi["is_loyalty"].astype(str).str.lower().isin(["true","1","t","yes"])
    else:
        oi["is_loyalty"] = False

    # Compute option_total and aggregate to order_id+lineitem_id
    oio["option_total"] = oio["option_price"] * oio["option_quantity"]
    opt_agg = oio.groupby(["order_id", "lineitem_id"], as_index=False)["option_total"].sum().rename(columns={"option_total":"options_total"})

    # Merge options into order items
    oi = oi.merge(opt_agg, on=["order_id","lineitem_id"], how="left")
    oi["options_total"] = pd.to_numeric(oi.get("options_total",0), errors="coerce").fillna(0.0)

    # Compute line_item_revenue
    oi["line_item_revenue"] = oi["item_price"] * oi["item_quantity"] + oi["options_total"]

    # Normalize date_dim: ensure date_key as string YYYYMMDD for joining (if date_key exists)
    if "date_key" in dd.columns:
        # coerce to datetime, then format
        dd["date_key_dt"] = pd.to_datetime(dd["date_key"], errors="coerce")
        dd["date_ymd"] = dd["date_key_dt"].dt.strftime("%Y%m%d").fillna("")
    else:
        dd["date_ymd"] = ""

    # Also create date_ymd in oi for join
    oi["date_ymd"] = oi["creation_time_utc"].dt.strftime("%Y%m%d")

    return oi, oio, dd

# load
order_items, order_item_options, date_dim = load_and_prepare()

# ---------------------------
# Build order-level summary
# ---------------------------
def build_order_level(df_lineitems):
    df = df_lineitems.copy()
    # Ensure datetime
    df["creation_time_utc"] = pd.to_datetime(df["creation_time_utc"], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    # Aggregate order-level
    order_level = df.groupby(["order_id","user_id","restaurant_id","is_loyalty"], as_index=False).agg(
        order_total = ("line_item_revenue","sum"),
        order_datetime = ("creation_time_utc","max")
    )
    # Fill numeric NaNs
    order_level["order_total"] = pd.to_numeric(order_level["order_total"], errors="coerce").fillna(0.0)
    order_level["order_datetime"] = pd.to_datetime(order_level["order_datetime"], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    # order_date for daily grouping (naive date)
    order_level["order_date"] = order_level["order_datetime"].dt.date
    # Convert ids to string
    order_level["user_id"] = order_level["user_id"].astype(str)
    order_level["order_id"] = order_level["order_id"].astype(str)
    order_level["restaurant_id"] = order_level["restaurant_id"].astype(str)
    return order_level

order_level_df = build_order_level(order_items)

# ---------------------------
# Primary Metric: CLV
# ---------------------------
def compute_clv_table(order_level):
    clv = order_level.groupby("user_id", as_index=False).agg(total_spend=("order_total","sum"))
    clv["total_spend"] = pd.to_numeric(clv["total_spend"], errors="coerce").fillna(0.0)
    # quantiles
    if len(clv) == 0:
        clv["clv_segment"] = ""
        return clv
    p20 = clv["total_spend"].quantile(0.20)
    p80 = clv["total_spend"].quantile(0.80)
    def seg(v):
        if v >= p80:
            return "High CLV"
        elif v <= p20:
            return "Low CLV"
        else:
            return "Mid CLV"
    clv["clv_segment"] = clv["total_spend"].apply(seg)
    clv["user_id"] = clv["user_id"].astype(str)
    return clv

# ---------------------------
# Secondary Metric: RFM (full history)
# ---------------------------
def compute_rfm_table(order_level, months=None):
    # reference date
    today = order_level["order_datetime"].max()
    if pd.isna(today):
        today = pd.Timestamp.now(tz="UTC")
    # filter months if provided
    if months is None:
        df = order_level.copy()
    else:
        cutoff = today - pd.DateOffset(months=months)
        df = order_level[order_level["order_datetime"] >= cutoff].copy()

    # aggregate per user
    rfm = df.groupby("user_id", as_index=False).agg(
        last_purchase=("order_datetime","max"),
        frequency=("order_id","nunique"),
        monetary=("order_total","sum")
    )
    # replace NaNs with zeros (per your request: NaN -> 0 in original dataset and RFM)
    rfm["frequency"] = pd.to_numeric(rfm["frequency"], errors="coerce").fillna(0).astype(int)
    rfm["monetary"] = pd.to_numeric(rfm["monetary"], errors="coerce").fillna(0.0)
    # last_purchase to datetime; fill NaT with epoch
    rfm["last_purchase"] = pd.to_datetime(rfm["last_purchase"], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    rfm["recency_days"] = (today - rfm["last_purchase"]).dt.days

    # percentile ranks 0..1 (robust to ties)
    rfm["recency_pct"] = rfm["recency_days"].rank(method="average", pct=True)
    rfm["frequency_pct"] = rfm["frequency"].rank(method="average", pct=True)
    rfm["monetary_pct"] = rfm["monetary"].rank(method="average", pct=True)
    # invert recency -> higher is better
    rfm["recency_inv_pct"] = 1.0 - rfm["recency_pct"]

    # map percentiles to scores 1..5 using np.digitize (avoids pd.cut duplicate-edge issues)
    bins = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    def pct_to_score(pct_series):
        # ensure in [0,1]
        arr = pct_series.clip(0.0,1.0).to_numpy()
        # np.digitize returns 1..5 for these bins when using bins[1:]
        scores = np.digitize(arr, bins[1:]) + 1  # produces 1..6? let's map carefully
        # Adjust: digitize on bins[1:] gives 0..5, so compute with searchsorted instead:
        scores = np.searchsorted(bins, arr, side='right')  # gives 1..5
        return scores.astype(int)

    rfm["r_score"] = pct_to_score(rfm["recency_inv_pct"])
    rfm["f_score"] = pct_to_score(rfm["frequency_pct"])
    rfm["m_score"] = pct_to_score(rfm["monetary_pct"])

    # ensure 1..5 bounds
    rfm["r_score"] = rfm["r_score"].clip(1,5)
    rfm["f_score"] = rfm["f_score"].clip(1,5)
    rfm["m_score"] = rfm["m_score"].clip(1,5)

    rfm["rfm_score"] = rfm["r_score"] + rfm["f_score"] + rfm["m_score"]

    # Segment definitions requested
    def rfm_segment(row):
        if (row["r_score"] >= 4) and (row["f_score"] >= 4) and (row["m_score"] >= 4):
            return "VIP"
        if (row["r_score"] >= 4) and (row["f_score"] <= 2):
            return "New Customer"
        if (row["r_score"] <= 2) and (row["f_score"] <= 2):
            return "Churn Risk"
        return "Other"

    rfm["rfm_segment"] = rfm.apply(rfm_segment, axis=1)
    rfm["user_id"] = rfm["user_id"].astype(str)
    return rfm

# ---------------------------
# Churn / Activity indicators
# ---------------------------
def compute_churn_indicators(order_level, lookback_days=30):
    today = order_level["order_datetime"].max()
    if pd.isna(today):
        today = pd.Timestamp.now(tz="UTC")
    # days since last
    last = order_level.groupby("user_id", as_index=False).agg(last_order=("order_datetime","max"))
    last["last_order"] = pd.to_datetime(last["last_order"], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    last["days_since_last_order"] = (today - last["last_order"]).dt.days
    # avg gap
    ol = order_level.sort_values(["user_id","order_datetime"]).copy()
    ol["prev_order"] = ol.groupby("user_id")["order_datetime"].shift(1)
    ol["gap_days"] = (ol["order_datetime"] - ol["prev_order"]).dt.days
    avg_gap = ol.groupby("user_id", as_index=False)["gap_days"].mean().rename(columns={"gap_days":"avg_gap_days"})
    avg_gap["avg_gap_days"] = avg_gap["avg_gap_days"].fillna(0)
    # percent change in spend: last lookback_days vs previous period
    p1_start = today - pd.Timedelta(days=lookback_days)
    p2_start = today - pd.Timedelta(days=2*lookback_days)
    spend_p1 = order_level[(order_level["order_datetime"] > p1_start) & (order_level["order_datetime"] <= today)].groupby("user_id", as_index=False)["order_total"].sum().rename(columns={"order_total":"spend_p1"})
    spend_p2 = order_level[(order_level["order_datetime"] > p2_start) & (order_level["order_datetime"] <= p1_start)].groupby("user_id", as_index=False)["order_total"].sum().rename(columns={"order_total":"spend_p2"})
    spend = spend_p1.merge(spend_p2, on="user_id", how="outer").fillna(0.0)
    spend["pct_change_spend"] = np.where(spend["spend_p2"] == 0, 0.0, (spend["spend_p1"] - spend["spend_p2"]) / spend["spend_p2"])
    # combine
    churn = last.merge(avg_gap, on="user_id", how="left").merge(spend[["user_id","spend_p1","spend_p2","pct_change_spend"]], on="user_id", how="left")
    churn["avg_gap_days"] = churn["avg_gap_days"].fillna(0.0)
    churn["at_risk"] = churn["days_since_last_order"] > 45
    churn["user_id"] = churn["user_id"].astype(str)
    return churn

# ---------------------------
# Time-based summaries
# ---------------------------
def compute_time_summaries(order_level, lineitems):
    # ensure datetime index
    ol = order_level.copy()
    ol["order_datetime"] = pd.to_datetime(ol["order_datetime"], errors="coerce", utc=True).fillna(pd.Timestamp("1970-01-01", tz="UTC"))
    ol = ol.set_index("order_datetime")
    if ol.empty:
        daily = pd.DataFrame(columns=["date","revenue"])
        weekly = pd.DataFrame(columns=["week_start","revenue"])
        monthly = pd.DataFrame(columns=["month_start","revenue"])
    else:
        daily = ol["order_total"].resample("D").sum().reset_index().rename(columns={"order_datetime":"date","order_total":"revenue"})
        weekly = ol["order_total"].resample("W").sum().reset_index().rename(columns={"order_datetime":"week_start","order_total":"revenue"})
        monthly = ol["order_total"].resample("M").sum().reset_index().rename(columns={"order_datetime":"month_start","order_total":"revenue"})
    # by location
    by_location = order_level.groupby("restaurant_id", as_index=False).agg(total_revenue=("order_total","sum"), avg_order_value=("order_total","mean"), orders_count=("order_id","nunique"))
    # by category: aggregate at lineitem level
    if "item_category" in lineitems.columns:
        by_category = lineitems.groupby("item_category", as_index=False).agg(total_revenue=("line_item_revenue","sum"))
    else:
        by_category = pd.DataFrame(columns=["item_category","total_revenue"])
    # convert ids to str
    by_location["restaurant_id"] = by_location["restaurant_id"].astype(str)
    return daily, weekly, monthly, by_location, by_category

# ---------------------------
# Loyalty impact
# ---------------------------
# def compute_loyalty_impact(order_level):
#     ol = order_level.copy()
#     # per-customer stats grouped by is_loyalty
#     cust = ol.groupby(["is_loyalty","user_id"], as_index=False).agg(
#         orders_count=("order_id","nunique"),
#         lifetime_value=("order_total","sum"),
#         avg_order_value=("order_total","mean")
#     )
#     summary = cust.groupby("is_loyalty", as_index=False).agg(
#         num_customers=("user_id","nunique"),
#         avg_orders_per_customer=("orders_count","mean"),
#         avg_lifetime_value=("lifetime_value","mean"),
#         median_ltv=("lifetime_value","median")
#     )
#     summary["is_loyalty"] = summary["is_loyalty"].astype(str)
#     return summary, cust

def compute_loyalty_impact(order_level, loyalty_filter=None):
    ol = order_level.copy()

    # --- APPLY FILTER (New) ---
    if loyalty_filter == "Loyal Customers":
        ol = ol[ol["is_loyalty"] == True]
    elif loyalty_filter == "Non-Loyal Customers":
        ol = ol[ol["is_loyalty"] == False]
    # "All" does nothing

    # --- ORIGINAL LOGIC (unchanged) ---
    cust = ol.groupby(["is_loyalty","user_id"], as_index=False).agg(
        orders_count=("order_id","nunique"),
        lifetime_value=("order_total","sum"),
        avg_order_value=("order_total","mean")
    )

    summary = cust.groupby("is_loyalty", as_index=False).agg(
        num_customers=("user_id","nunique"),
        avg_orders_per_customer=("orders_count","mean"),
        avg_lifetime_value=("lifetime_value","mean"),
        median_ltv=("lifetime_value","median")
    )

    summary["is_loyalty"] = summary["is_loyalty"].astype(str)

    return summary, cust


# ---------------------------
# Discount effectiveness
# ---------------------------
# def compute_discount_effectiveness(lineitems, order_level):
#     li = lineitems.copy()
#     li["is_discounted_item"] = (li["options_total"] < 0) | (li["item_price"] < 0)
#     order_discount = li.groupby("order_id", as_index=False)["is_discounted_item"].max().rename(columns={"is_discounted_item":"order_has_discount"})
#     orders = order_level.merge(order_discount, on="order_id", how="left")
#     orders["order_has_discount"] = orders["order_has_discount"].fillna(False)
#     summary = orders.groupby("order_has_discount", as_index=False).agg(num_orders=("order_id","nunique"), revenue=("order_total","sum"), avg_order_value=("order_total","mean"))
#     return summary, orders

# ---------------------------
# Compute all tables (once)
# ---------------------------
clv_table = compute_clv_table(order_level_df)
rfm_table = compute_rfm_table(order_level_df, months=None)   # full history
churn_table = compute_churn_indicators(order_level_df)
daily, weekly, monthly, by_location, by_category = compute_time_summaries(order_level_df, order_items)
loyalty_summary, loyalty_customers = compute_loyalty_impact(order_level_df)
#discount_summary, orders_with_discount_flag = compute_discount_effectiveness(order_items, order_level_df)

# ---------------------------
# Streamlit UI: Tabs & Filters
# ---------------------------
st.title("Global Partners — Customer Intelligence Dashboard")

# Sidebar filters (global)
st.sidebar.header("Global Filters")
location_choices = sorted(order_items["restaurant_id"].unique().astype(str).tolist())
selected_locations = st.sidebar.multiselect("Restaurant (Location) filter (optional)", options=location_choices, default=location_choices)

# Apply location filter to order-level derived tables where appropriate
if selected_locations:
    # subset order_level_df and order_items for downstream tables
    order_level_filtered = order_level_df[order_level_df["restaurant_id"].isin(selected_locations)]
    order_items_filtered = order_items[order_items["restaurant_id"].isin(selected_locations)]
else:
    order_level_filtered = order_level_df.copy()
    order_items_filtered = order_items.copy()

# Tabs (keep first 3 unchanged as requested)
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([   #, tab7
    "CLV", "RFM", "Activity Risk", "Time Summaries", "Loyalty Impact", "Locations"  #, "Discounts"
])

# --------------------------- CLV tab ---------------------------
with tab1:
    st.header("Customer Lifetime Value (CLV)")
    st.write("Filter by CLV segment (High / Mid / Low)")

    clv_filter = st.selectbox("CLV Segment", options=["All","High CLV","Mid CLV","Low CLV"], index=0)
    if clv_filter == "All":
        display_clv = clv_table.copy()
    else:
        display_clv = clv_table[clv_table["clv_segment"] == clv_filter].copy()

    # ensure user_id string
    display_clv["user_id"] = display_clv["user_id"].astype(str)
    st.dataframe(display_clv.reset_index(drop=True))

# --------------------------- RFM tab ---------------------------
with tab2:
    st.header("RFM Segmentation")
    st.write("Filter by RFM segment (VIP / New Customer / Churn Risk)")

    rfm_filter = st.selectbox("RFM Segment", options=["All","VIP","New Customer","Churn Risk"], index=0)
    if rfm_filter == "All":
        display_rfm = rfm_table.copy()
    else:
        display_rfm = rfm_table[rfm_table["rfm_segment"] == rfm_filter].copy()

    display_rfm["user_id"] = display_rfm["user_id"].astype(str)
    st.dataframe(display_rfm.reset_index(drop=True))

# --------------------------- Activity Risk tab ---------------------------
with tab3:
    st.header("Customer Activity & At-Risk Profiling")
    st.write("Filter: At Risk / Active (At Risk defined as days_since_last_order > 45)")

    risk_filter = st.selectbox("Risk Filter", options=["All","At Risk","Active"], index=0)
    if risk_filter == "All":
        display_churn = churn_table.copy()
    elif risk_filter == "At Risk":
        display_churn = churn_table[churn_table["at_risk"] == True].copy()
    else:
        display_churn = churn_table[churn_table["at_risk"] == False].copy()

    display_churn["user_id"] = display_churn["user_id"].astype(str)
    st.dataframe(display_churn.reset_index(drop=True))

# --------------------------- Time Summaries tab ---------------------------
# with tab4:
#     st.header("Time-Based Revenue Summaries")

#     # --- NEW: Holiday filter ---
#     st.subheader("Holiday Filter")
#     holiday_filter = st.radio(
#         "Show:", 
#         ["All Days", "Holidays Only", "Non-Holidays Only"], 
#         index=0
#     )

#     # Prepare holiday join key
#     dd_temp = date_dim.copy()
#     dd_temp["date_ymd"] = dd_temp.get("date_ymd", dd_temp["date_key_dt"].dt.strftime("%Y%m%d"))

#     # Attach is_holiday to order_level_filtered
#     ol = order_level_filtered.copy()
#     ol["date_ymd"] = ol["order_datetime"].dt.strftime("%Y%m%d")
#     ol = ol.merge(dd_temp[["date_ymd", "is_holiday"]], on="date_ymd", how="left")

#     # Clean holiday values
#     ol["is_holiday"] = ol["is_holiday"].astype(str).str.lower().isin(["true", "1", "yes"])

#     # Apply holiday filter
#     if holiday_filter == "Holidays Only":
#         ol = ol[ol["is_holiday"] == True]
#     elif holiday_filter == "Non-Holidays Only":
#         ol = ol[ol["is_holiday"] == False]

#     # Now continue as before...
#     st.subheader("Daily Revenue (filtered by location + holiday filter)")
#     ol["order_datetime"] = pd.to_datetime(
#         ol["order_datetime"], errors="coerce", utc=True
#     ).fillna(pd.Timestamp("1970-01-01", tz="UTC"))

#     daily_filt = (
#         ol.set_index("order_datetime")["order_total"]
#         .resample("D").sum()
#         .reset_index()
#         .rename(columns={"order_datetime": "date", "order_total": "revenue"})
#     )
#     st.dataframe(daily_filt.tail(60).reset_index(drop=True))

#     st.subheader("Weekly Revenue")
#     weekly_filt = (
#         ol.set_index("order_datetime")["order_total"]
#         .resample("W").sum()
#         .reset_index()
#         .rename(columns={"order_datetime": "week_start", "order_total": "revenue"})
#     )
#     st.dataframe(weekly_filt.tail(52).reset_index(drop=True))

#     st.subheader("Monthly Revenue")
#     monthly_filt = (
#         ol.set_index("order_datetime")["order_total"]
#         .resample("M").sum()
#         .reset_index()
#         .rename(columns={"order_datetime": "month_start", "order_total": "revenue"})
#     )
#     st.dataframe(monthly_filt.tail(36).reset_index(drop=True))

#     st.subheader("Revenue by Location (RESTAURANT_ID)")
#     by_loc_filt = ol.groupby("restaurant_id", as_index=False).agg(
#         total_revenue=("order_total", "sum"),
#         avg_order_value=("order_total", "mean"),
#         orders_count=("order_id", "nunique"),
#     )
#     st.dataframe(
#         by_loc_filt.sort_values("total_revenue", ascending=False).reset_index(drop=True)
#     )

#     st.subheader("Revenue by Menu Category (if available)")
#     if not by_category.empty:
#         if (
#             not order_items_filtered.empty
#             and "item_category" in order_items_filtered.columns
#         ):
#             cat_filt = (
#                 order_items_filtered.groupby("item_category", as_index=False)
#                 .agg(total_revenue=("line_item_revenue", "sum"))
#             )
#             st.dataframe(
#                 cat_filt.sort_values("total_revenue", ascending=False).reset_index(drop=True)
#             )
#         else:
#             st.dataframe(by_category)
#     else:
#         st.write("No item_category column available in data.")
with tab4:
    st.header("Time-Based Revenue Summaries")

    # -------------------------------------------------------------------------
    # 1) HOLIDAY FILTER
    # -------------------------------------------------------------------------
    st.subheader("Holiday Filter")
    holiday_filter = st.radio(
        "Select Holiday Filter:",
        ["All Days", "Holidays Only", "Non-Holidays Only"],
        index=0
    )

    # -------------------------------------------------------------------------
    # 2) WEEKEND FILTER
    # -------------------------------------------------------------------------
    st.subheader("Weekend Filter")
    weekend_filter = st.radio(
        "Select Weekend Filter:",
        ["All Days", "Weekends Only", "Weekdays Only"],
        index=0
    )

    # -------------------------------------------------------------------------
    # PREPARE DATE KEYS
    # -------------------------------------------------------------------------
    dd_temp = date_dim.copy()

    # Ensure date_key_dt exists and is datetime
    if "date_key_dt" not in dd_temp.columns:
        dd_temp["date_key_dt"] = pd.to_datetime(dd_temp["date_key"], errors="coerce")

    dd_temp["date_ymd"] = dd_temp["date_key_dt"].dt.strftime("%Y%m%d")

    # Attach holiday + weekend indicators to order_level_filtered
    ol = order_level_filtered.copy()
    ol["date_ymd"] = ol["order_datetime"].dt.strftime("%Y%m%d")

    ol = ol.merge(
        dd_temp[["date_ymd", "is_holiday", "is_weekend"]],
        on="date_ymd",
        how="left"
    )

    # Clean boolean columns
    ol["is_holiday"] = ol["is_holiday"].astype(str).str.lower().isin(["true", "1", "yes"])
    ol["is_weekend"] = ol["is_weekend"].astype(str).str.lower().isin(["true", "1", "yes"])

    # -------------------------------------------------------------------------
    # APPLY FILTERS
    # -------------------------------------------------------------------------

    # Holiday filter
    if holiday_filter == "Holidays Only":
        ol = ol[ol["is_holiday"] == True]
    elif holiday_filter == "Non-Holidays Only":
        ol = ol[ol["is_holiday"] == False]

    # Weekend filter
    if weekend_filter == "Weekends Only":
        ol = ol[ol["is_weekend"] == True]
    elif weekend_filter == "Weekdays Only":
        ol = ol[ol["is_weekend"] == False]

    # -------------------------------------------------------------------------
    # CONTINUE WITH ORIGINAL SUMMARIES
    # -------------------------------------------------------------------------
    st.subheader("Daily Revenue (Filtered)")

    ol["order_datetime"] = pd.to_datetime(
        ol["order_datetime"], errors="coerce", utc=True
    ).fillna(pd.Timestamp("1970-01-01", tz="UTC"))

    daily_filt = (
        ol.set_index("order_datetime")["order_total"]
        .resample("D").sum()
        .reset_index()
        .rename(columns={"order_datetime": "date", "order_total": "revenue"})
    )
    st.dataframe(daily_filt.tail(60).reset_index(drop=True))

    st.subheader("Weekly Revenue")
    weekly_filt = (
        ol.set_index("order_datetime")["order_total"]
        .resample("W").sum()
        .reset_index()
        .rename(columns={"order_datetime": "week_start", "order_total": "revenue"})
    )
    st.dataframe(weekly_filt.tail(52).reset_index(drop=True))

    st.subheader("Monthly Revenue")
    monthly_filt = (
        ol.set_index("order_datetime")["order_total"]
        .resample("M").sum()
        .reset_index()
        .rename(columns={"order_datetime": "month_start", "order_total": "revenue"})
    )
    st.dataframe(monthly_filt.tail(36).reset_index(drop=True))

    st.subheader("Revenue by Location (RESTAURANT_ID)")
    by_loc_filt = ol.groupby("restaurant_id", as_index=False).agg(
        total_revenue=("order_total", "sum"),
        avg_order_value=("order_total", "mean"),
        orders_count=("order_id", "nunique"),
    )
    st.dataframe(
        by_loc_filt.sort_values("total_revenue", ascending=False).reset_index(drop=True)
    )

    st.subheader("Revenue by Menu Category (if available)")
    if not by_category.empty:
        if (
            not order_items_filtered.empty
            and "item_category" in order_items_filtered.columns
        ):
            cat_filt = (
                order_items_filtered.groupby("item_category", as_index=False)
                .agg(total_revenue=("line_item_revenue", "sum"))
            )
            st.dataframe(
                cat_filt.sort_values("total_revenue", ascending=False).reset_index(drop=True)
            )
        else:
            st.dataframe(by_category)
    else:
        st.write("No item_category column available in data.")


# --------------------------- Loyalty Impact tab ---------------------------
# with tab5:
#     st.header("Loyalty Program Impact")
#     loyalty_summary_filt, loyalty_customers_filt = compute_loyalty_impact(order_level_filtered)
#     st.subheader("Loyalty Summary (loyal vs non-loyal)")
#     st.dataframe(loyalty_summary_filt)
#     st.subheader("Loyalty customers sample")
#     st.dataframe(loyalty_customers_filt.head(200))
with tab5:
    st.header("Loyalty Program Impact")

    # --- ADD FILTER FOR IS_LOYALTY ---
    st.subheader("Filter: Loyalty Status")
    loyalty_filter = st.selectbox(
        "Select Loyalty Group",
        ["All", "Loyal", "Non-Loyal"]
    )

    # apply the loyalty filter BEFORE computing metrics
    if loyalty_filter == "Loyal":
        order_level_loyal_filt = order_level_filtered[order_level_filtered["is_loyalty"] == True]
    elif loyalty_filter == "Non-Loyal":
        order_level_loyal_filt = order_level_filtered[order_level_filtered["is_loyalty"] == False]
    else:
        order_level_loyal_filt = order_level_filtered.copy()

    # compute loyalty metrics on the filtered dataset
    loyalty_summary_filt, loyalty_customers_filt = compute_loyalty_impact(order_level_loyal_filt)

    st.subheader("Loyalty Summary (loyal vs non-loyal)")
    st.dataframe(loyalty_summary_filt)

    st.subheader("Loyalty Customers Sample")
    st.dataframe(loyalty_customers_filt.head(200))

# --------------------------- Locations tab ---------------------------
with tab6:
    st.header("Top Performing Locations")
    by_loc = order_level_filtered.groupby("restaurant_id", as_index=False).agg(total_revenue=("order_total","sum"), avg_order_value=("order_total","mean"), orders_per_day=("order_total", lambda x: x.count()))
    st.dataframe(by_loc.sort_values("total_revenue", ascending=False).reset_index(drop=True))

# --------------------------- Discounts tab ---------------------------
# with tab7:
#     st.header("Pricing & Discount Effectiveness")
#     discount_summary_filt, orders_with_discount_flag_filt = compute_discount_effectiveness(order_items_filtered, order_level_filtered)
#     st.subheader("Summary: Discounted vs Non-discounted Orders")
#     st.dataframe(discount_summary_filt)
#     st.subheader("Sample orders (first 200) with discount flag")
#     st.dataframe(orders_with_discount_flag_filt.head(200))

# Footer notes
st.markdown("""
**Notes**
Full Streamlit dashboard (single file) implementing:
 - CLV (High / Mid / Low) with filter
 - RFM (VIP / New Customer / Churn Risk) with filter
 - Activity / At-Risk filter (>45 days)
 - Time summaries (daily/weekly/monthly), by location & category
 - Loyalty impact table
 - Location performance table
""")



