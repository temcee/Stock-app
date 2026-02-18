import streamlit as st
import yfinance as yf
import pandas as pd
import time
import json
import gspread
import requests
import io
import plotly.graph_objects as go
from datetime import date
from google.oauth2.service_account import Credentials
from yfinance.exceptions import YFRateLimitError

# --------------------
# 初期設定
# --------------------
st.set_page_config(layout="wide")
st.title("📈 株式銘柄管理ツール")

SPREADSHEET_ID   = "1noyNkmaeisqi96_xAFS-yo18pqtcWOu8yOpDzzOKnhg"
SHEET_WATCH      = "シート1"
SHEET_HOLDINGS   = "holdings"
SHEET_HISTORY    = "asset_history"
SHEET_TRADES     = "trade_history"
SHEET_SNAPSHOT   = "quarterly_snapshot"
SHEET_CASH       = "cash_balance"

WATCH_COLS    = ["コード", "銘柄名", "株価", "PER", "PBR", "ROE", "配当",
                 "四季報", "タグ", "メモ", "目標株価", "削除"]
HOLDING_COLS  = ["コード", "銘柄名", "取得単価", "枚数"]
HISTORY_COLS  = ["日付", "総資産", "損益合計", "ルックスルー利益"]
TRADE_COLS    = ["日付", "コード", "銘柄名", "売買", "単価", "枚数", "金額", "メモ"]
SNAPSHOT_COLS = ["日付", "コード", "銘柄名", "株価", "PER", "PBR", "ROE(%)"]
CASH_COLS     = ["現金残高"]

# --------------------
# 東証銘柄名マスタ
# --------------------
@st.cache_data(ttl=86400)
def load_tse_master():
    try:
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        r = requests.get(url, timeout=10)
        xls = pd.read_excel(io.BytesIO(r.content), header=0)
        code_col = [c for c in xls.columns if "コード" in str(c)][0]
        name_col = [c for c in xls.columns if "銘柄名" in str(c)][0]
        return dict(zip(xls[code_col].astype(str).str.zfill(4), xls[name_col]))
    except Exception:
        return {}

# --------------------
# Google Sheets接続
# --------------------
def get_spreadsheet():
    creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(spreadsheet, name, columns):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=name, rows=1000, cols=20)
        sheet.append_row(columns)
        return sheet

def load_df(sheet, columns):
    values = sheet.get_all_values()
    if len(values) <= 1:
        return pd.DataFrame(columns=columns)
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    if "削除" in df.columns:
        df["削除"] = df["削除"].apply(lambda x: str(x).upper() == "TRUE")
    return df

def save_df(sheet, df, retries=3):
    save = df.copy()
    save = save.fillna("")
    for col in save.columns:
        if col != "削除":
            save[col] = save[col].apply(
                lambda x: int(x) if isinstance(x, float) and not pd.isna(x) and x == int(x) else x
            )
    if "削除" in save.columns:
        save["削除"] = save["削除"].apply(
            lambda x: "TRUE" if x is True or str(x).upper() == "TRUE" else "FALSE"
        )
    for attempt in range(retries):
        try:
            sheet.clear()
            sheet.update([save.columns.tolist()] + save.values.tolist())
            return
        except gspread.exceptions.APIError:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                raise

# --------------------
# 共通関数
# --------------------
@st.cache_data(ttl=3600)
def fetch_stock_data(code):
    try:
        time.sleep(1)
        ticker = yf.Ticker(code)
        info = ticker.info
        name = info.get("longName") or info.get("shortName") or ""
        if code.endswith(".T"):
            raw = code.replace(".T", "").zfill(4)
            jp_name = load_tse_master().get(raw, "")
            if jp_name:
                name = jp_name
        price = info.get("currentPrice")
        per   = info.get("trailingPE")
        pbr   = info.get("priceToBook")
        roe   = info.get("returnOnEquity")
        if roe is not None:
            roe *= 100
        div = info.get("dividendYield")
        eps = info.get("trailingEps")
        return name, price, per, pbr, roe, div, eps
    except YFRateLimitError as e:
        st.warning(f"レート制限エラー: {code} - {str(e)}")
        return "", None, None, None, None, None, None
    except Exception as e:
        st.warning(f"データ取得エラー: {code} - {str(e)}")
        return "", None, None, None, None, None, None

def normalize_tags(tag_str):
    if not isinstance(tag_str, str):
        return ""
    return " ".join(tag_str.replace("　", " ").split())

def normalize_code(code):
    code = str(code).strip().upper()
    if "." in code:
        return code
    if len(code) <= 5:
        return f"{code}.T"
    return code

def get_ir_links(code):
    raw = code.upper().replace(".T", "").strip()
    return (f"https://ir-searcher.com/kobetsu.php?code={raw}",
            f"https://irbank.net/{raw}")

def format_watch_df(df):
    view = df.copy()
    for col in ["株価", "PER", "PBR", "ROE", "配当"]:
        if col in view.columns:
            view[col] = pd.to_numeric(view[col], errors="coerce")
    view["株価"]   = view["株価"].round(0)
    view["PER"]    = view["PER"].round(1)
    view["PBR"]    = view["PBR"].round(1)
    view["ROE(%)"] = view["ROE"].round(1)
    if "配当" in view.columns:
        view["配当"] = view["配当"].round(2)
    view["IR Searcher"] = view["コード"].apply(lambda c: get_ir_links(c)[0])
    view["irbank"]      = view["コード"].apply(lambda c: get_ir_links(c)[1])
    view.drop(columns=["ROE"], inplace=True)
    col_order = ["コード", "銘柄名", "株価", "PER", "PBR", "ROE(%)", "配当",
                 "四季報", "目標株価", "タグ", "メモ", "IR Searcher", "irbank", "削除"]
    return view[[c for c in col_order if c in view.columns]]

def get_all_tags(df):
    tags = set()
    for t in df["タグ"]:
        if isinstance(t, str):
            tags.update(t.split())
    return sorted(tags)

# --------------------
# シート接続
# --------------------
spreadsheet    = get_spreadsheet()
watch_sheet    = get_or_create_sheet(spreadsheet, SHEET_WATCH,     WATCH_COLS)
holding_sheet  = get_or_create_sheet(spreadsheet, SHEET_HOLDINGS,  HOLDING_COLS)
history_sheet  = get_or_create_sheet(spreadsheet, SHEET_HISTORY,   HISTORY_COLS)
trade_sheet    = get_or_create_sheet(spreadsheet, SHEET_TRADES,    TRADE_COLS)
snapshot_sheet = get_or_create_sheet(spreadsheet, SHEET_SNAPSHOT,  SNAPSHOT_COLS)
cash_sheet     = get_or_create_sheet(spreadsheet, SHEET_CASH,      CASH_COLS)

# --------------------
# タブ
# --------------------
tab1, tab2, tab3 = st.tabs(["📋 ウォッチリスト", "💼 保有株", "📒 売買履歴"])

# ====================
# TAB1: ウォッチリスト
# ====================
with tab1:
    watch_df = load_df(watch_sheet, WATCH_COLS)

    defaults = {"銘柄名": "", "四季報": 0, "配当": None,
                "タグ": "", "メモ": "", "目標株価": None, "削除": False}
    for col, val in defaults.items():
        if col not in watch_df.columns:
            watch_df[col] = val
    for col in ["タグ", "メモ", "銘柄名"]:
        watch_df[col] = watch_df[col].astype(str).fillna("")

    # 銘柄追加
    st.subheader("➕ 銘柄を追加")
    raw_code = st.text_input("銘柄コード（例：7203 / AAPL）")
    code = normalize_code(raw_code)

    if st.button("銘柄を追加"):
        name, price, per, pbr, roe, div, _ = fetch_stock_data(code)
        if code in watch_df["コード"].values:
            watch_df.loc[watch_df["コード"] == code, "四季報"] += 1
            if name:
                watch_df.loc[watch_df["コード"] == code, "銘柄名"] = name
            st.info("既存銘柄のため、四季報を +1 しました")
        else:
            watch_df = pd.concat([watch_df, pd.DataFrame([{
                "コード": code, "銘柄名": name, "株価": price,
                "PER": per, "PBR": pbr, "ROE": roe, "配当": div,
                "四季報": 1, "タグ": "", "メモ": "", "目標株価": None, "削除": False
            }])], ignore_index=True)
            st.success("追加しました")
        save_df(watch_sheet, watch_df)
        st.rerun()

    st.divider()

    # CSV追加
    st.subheader("📂 CSVから銘柄を追加")
    uploaded_file = st.file_uploader("code列を持つCSV", type="csv")
    if uploaded_file:
        add_df = pd.read_csv(uploaded_file)
        if "code" not in add_df.columns and "コード" not in add_df.columns:
            st.error("CSVに code 列または コード 列がありません")
        else:
            code_col = "code" if "code" in add_df.columns else "コード"
            for rc in add_df[code_col]:
                c = normalize_code(rc)
                if c not in watch_df["コード"].values:
                    name, price, per, pbr, roe, div, _ = fetch_stock_data(c)
                    watch_df = pd.concat([watch_df, pd.DataFrame([{
                        "コード": c, "銘柄名": name, "株価": price,
                        "PER": per, "PBR": pbr, "ROE": roe, "配当": div,
                        "四季報": 1, "タグ": "", "メモ": "", "目標株価": None, "削除": False
                    }])], ignore_index=True)
            save_df(watch_sheet, watch_df)
            st.success("CSV追加完了")

    st.divider()

    # 一覧表示
    st.subheader("📊 登録銘柄一覧")
    sort_col  = st.selectbox("並び替え", ["株価", "PER", "PBR", "ROE(%)", "配当"])
    ascending = st.checkbox("昇順", False)

    for col in ["株価", "PER", "PBR", "ROE", "配当"]:
        if col in watch_df.columns:
            watch_df[col] = pd.to_numeric(watch_df[col], errors="coerce")

    watch_df = watch_df.sort_values(
        by="ROE" if sort_col == "ROE(%)" else sort_col,
        ascending=ascending, na_position="last"
    )

    view_df = format_watch_df(watch_df)
    edited_df = st.data_editor(
        view_df, use_container_width=True,
        column_config={
            "タグ": st.column_config.TextColumn(help="スペース区切りで複数指定"),
            "メモ": st.column_config.TextColumn(width="large"),
            "IR Searcher": st.column_config.LinkColumn(display_text="🔍 IR Searcher", disabled=True),
            "irbank":       st.column_config.LinkColumn(display_text="📊 irbank",       disabled=True),
        }
    )
    edited_df["ROE"] = edited_df["ROE(%)"]
    edited_df.drop(columns=["ROE(%)", "IR Searcher", "irbank"], inplace=True)

    st.subheader("🏷️ タグで絞り込み")
    all_tags      = get_all_tags(watch_df)
    selected_tags = st.multiselect("タグを選択", all_tags)
    if selected_tags:
        watch_df = watch_df[watch_df["タグ"].apply(
            lambda x: all(tag in x.split() for tag in selected_tags)
        )]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("編集内容を保存"):
            edited_df["タグ"] = edited_df["タグ"].apply(normalize_tags)
            save_df(watch_sheet, edited_df)
            st.success("保存しました")
            st.rerun()
    with col2:
        if st.button("選択した銘柄を削除"):
            save_df(watch_sheet, edited_df[edited_df["削除"] != True].assign(削除=False))
            st.success("削除しました")
            st.rerun()
    with col3:
        if st.button("全銘柄を更新"):
            for i, row in watch_df.iterrows():
                name, price, per, pbr, roe, div, _ = fetch_stock_data(row["コード"])
                if name:
                    watch_df.loc[i, "銘柄名"] = name
                watch_df.loc[i, ["株価", "PER", "PBR", "ROE", "配当"]] = [price, per, pbr, roe, div]
            save_df(watch_sheet, watch_df)
            st.success("更新しました")
    with col4:
        if st.button("銘柄名を日本語に更新"):
            master = load_tse_master()
            for i, row in watch_df.iterrows():
                if row["コード"].endswith(".T"):
                    jp = master.get(row["コード"].replace(".T", "").zfill(4), "")
                    if jp:
                        watch_df.loc[i, "銘柄名"] = jp
            save_df(watch_sheet, watch_df)
            st.success("日本語銘柄名に更新しました")
            st.rerun()

# ====================
# TAB2: 保有株
# ====================
with tab2:
    # ----------
    # 現金残高の取得
    # ----------
    cash_df = load_df(cash_sheet, CASH_COLS)
    if len(cash_df) == 0:
        cash_balance = 0
    else:
        cash_balance = pd.to_numeric(cash_df.iloc[0]["現金残高"], errors="coerce") or 0

    st.subheader("💰 現金残高")
    col_cash1, col_cash2 = st.columns([3, 1])
    with col_cash1:
        new_cash = st.number_input(
            "現金残高（円）",
            min_value=0,
            value=int(cash_balance),
            step=10000,
            key="cash_input"
        )
    with col_cash2:
        st.write("")
        st.write("")
        if st.button("残高を更新"):
            save_df(cash_sheet, pd.DataFrame([{"現金残高": new_cash}]))
            st.success(f"現金残高を ¥{new_cash:,} に更新しました")
            st.rerun()

    st.divider()

    # ----------
    # 保有株データ読み込み
    # ----------
    holding_df = load_df(holding_sheet, HOLDING_COLS)
    history_df = load_df(history_sheet, HISTORY_COLS)

    # コードを正規化（.T付与）
    if "コード" in holding_df.columns:
        holding_df["コード"] = holding_df["コード"].apply(normalize_code)

    for col in ["取得単価", "枚数"]:
        if col in holding_df.columns:
            holding_df[col] = pd.to_numeric(holding_df[col], errors="coerce")

    # ----------
    # 株価・指標・銘柄名を取得してDataFrameに結合
    # ----------
    names, prices, pers, pbrs, roes, epss = {}, {}, {}, {}, {}, {}
    for _, row in holding_df.iterrows():
        c = row["コード"]
        name, price, per, pbr, roe, _, eps = fetch_stock_data(c)
        names[c]  = name or row.get("銘柄名", "")
        prices[c] = price or 0
        pers[c]   = per
        pbrs[c]   = pbr
        roes[c]   = roe
        epss[c]   = eps or 0

    holding_df["銘柄名"] = holding_df["コード"].map(names)
    holding_df["株価"]   = holding_df["コード"].map(prices)
    holding_df["PER"]    = holding_df["コード"].map(pers)
    holding_df["PBR"]    = holding_df["コード"].map(pbrs)
    holding_df["ROE(%)"] = holding_df["コード"].map(roes)
    holding_df["時価"]   = holding_df["株価"] * holding_df["枚数"]
    holding_df["損益"]   = (holding_df["株価"] - holding_df["取得単価"]) * holding_df["枚数"]
    holding_df["EPS"]    = holding_df["コード"].map(epss)
    holding_df["ルックスルー利益"] = holding_df["EPS"] * holding_df["枚数"]

    # ----------
    # サマリー
    # ----------
    stock_value = holding_df["時価"].sum()
    total_asset = stock_value + cash_balance
    total_pnl   = holding_df["損益"].sum()
    total_lt    = holding_df["ルックスルー利益"].sum()

    st.subheader("📊 ポートフォリオサマリー")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("総資産", f"¥{total_asset:,.0f}")
    c2.metric("　株式時価", f"¥{stock_value:,.0f}")
    c3.metric("損益合計", f"¥{total_pnl:,.0f}")
    c4.metric("ルックスルー利益", f"¥{total_lt:,.0f}")

    # ----------
    # 総資産の自動記録（当日分がなければ記録）
    # ----------
    today_str = date.today().isoformat()
    history_df["日付"] = history_df["日付"].astype(str)
    if today_str not in history_df["日付"].values and total_asset > 0:
        new_row = pd.DataFrame([{
            "日付": today_str,
            "総資産": round(total_asset, 0),
            "損益合計": round(total_pnl, 0),
            "ルックスルー利益": round(total_lt, 0)
        }])
        history_df = pd.concat([history_df, new_row], ignore_index=True)
        save_df(history_sheet, history_df)

    # ----------
    # 四半期スナップショット自動記録
    # ----------
    today = date.today()
    is_quarter_end = today.month in [3, 6, 9, 12]
    snapshot_df = load_df(snapshot_sheet, SNAPSHOT_COLS)
    snapshot_df["日付"] = snapshot_df["日付"].astype(str)
    quarter_key = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    already_snapped = any(quarter_key in d for d in snapshot_df["日付"].values)

    if is_quarter_end and not already_snapped and len(holding_df) > 0:
        snap_rows = []
        for _, row in holding_df.iterrows():
            snap_rows.append({
                "日付":   f"{today_str}({quarter_key})",
                "コード": row["コード"],
                "銘柄名": row["銘柄名"],
                "株価":   round(float(row["株価"]), 0) if pd.notna(row["株価"]) else "",
                "PER":    round(float(row["PER"]),  1) if pd.notna(row["PER"])  else "",
                "PBR":    round(float(row["PBR"]),  1) if pd.notna(row["PBR"])  else "",
                "ROE(%)": round(float(row["ROE(%)"]), 1) if pd.notna(row["ROE(%)"]) else "",
            })
        snapshot_df = pd.concat([snapshot_df, pd.DataFrame(snap_rows)], ignore_index=True)
        save_df(snapshot_sheet, snapshot_df)
        st.toast(f"📸 {quarter_key} の四半期スナップショットを記録しました")

    st.divider()

    # ----------
    # 保有株一覧
    # ----------
    st.subheader("💼 保有株一覧")

    display_cols = ["コード", "銘柄名", "株価", "PER", "PBR", "ROE(%)",
                    "取得単価", "枚数", "時価", "損益", "ルックスルー利益"]
    view_holding = holding_df[display_cols].copy()
    for col in ["株価", "PER", "PBR", "ROE(%)"]:
        view_holding[col] = pd.to_numeric(view_holding[col], errors="coerce").round(1)
    view_holding["取得単価"] = view_holding["取得単価"].round(0)
    view_holding["時価"]     = view_holding["時価"].round(0)
    view_holding["損益"]     = view_holding["損益"].round(0)
    view_holding["ルックスルー利益"] = view_holding["ルックスルー利益"].round(0)

    edited_holding = st.data_editor(
        view_holding, use_container_width=True,
        column_config={
            "取得単価": st.column_config.NumberColumn(format="¥%.0f"),
            "枚数":     st.column_config.NumberColumn(),
            "時価":     st.column_config.NumberColumn(format="¥%.0f", disabled=True),
            "損益":     st.column_config.NumberColumn(format="¥%.0f", disabled=True),
            "ルックスルー利益": st.column_config.NumberColumn(format="¥%.0f", disabled=True),
        },
        num_rows="dynamic"
    )

    if st.button("保有株を保存"):
        save_holding = edited_holding[["コード", "銘柄名", "取得単価", "枚数"]].copy()
        save_holding["コード"] = save_holding["コード"].apply(normalize_code)
        save_df(holding_sheet, save_holding)
        st.success("保存しました")
        st.rerun()

    st.divider()

    # ----------
    # 総資産グラフ
    # ----------
    st.subheader("📈 総資産推移")

    if len(history_df) > 0:
        history_df["日付"]   = pd.to_datetime(history_df["日付"])
        history_df["総資産"] = pd.to_numeric(history_df["総資産"], errors="coerce")
        history_df["損益合計"] = pd.to_numeric(history_df["損益合計"], errors="coerce")

        # 期間フィルター
        min_date = history_df["日付"].min().date()
        max_date = history_df["日付"].max().date()
        col_a, col_b = st.columns(2)
        with col_a:
            start_date = st.date_input("開始日", value=min_date, min_value=min_date, max_value=max_date)
        with col_b:
            end_date   = st.date_input("終了日", value=max_date, min_value=min_date, max_value=max_date)

        filtered = history_df[
            (history_df["日付"].dt.date >= start_date) &
            (history_df["日付"].dt.date <= end_date)
        ].sort_values("日付")

        if len(filtered) > 0:
            fig = go.Figure()

            # 総資産（左軸）
            fig.add_trace(go.Scatter(
                x=filtered["日付"],
                y=filtered["総資産"],
                name="総資産",
                line=dict(color="#1f77b4", width=2),
                yaxis="y1"
            ))

            # 損益合計（右軸）
            fig.add_trace(go.Scatter(
                x=filtered["日付"],
                y=filtered["損益合計"],
                name="損益合計",
                line=dict(color="#ff7f0e", width=2),
                yaxis="y2"
            ))

            fig.update_layout(
                height=400,
                margin=dict(l=10, r=10, t=30, b=10),
                yaxis=dict(
                    title="総資産（円）",
                    tickformat=",",
                    side="left"
                ),
                yaxis2=dict(
                    title="損益合計（円）",
                    tickformat=",",
                    side="right",
                    overlaying="y"
                ),
                legend=dict(orientation="h", y=1.1),
                xaxis=dict(fixedrange=True),
            )
            st.plotly_chart(fig, use_container_width=True, config={"staticPlot": True})
        else:
            st.info("指定期間のデータがありません")
    else:
        st.info("まだ記録がありません。保有株を登録するとアプリを開くたびに自動記録されます。")

# ====================
# TAB3: 売買履歴
# ====================
with tab3:
    trade_df = load_df(trade_sheet, TRADE_COLS)

    # ----------
    # 売買記録の入力
    # ----------
    st.subheader("➕ 売買を記録")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        t_date  = st.date_input("取引日", value=date.today())
        t_code  = st.text_input("銘柄コード（例：7203）", key="trade_code")
    with col_b:
        t_type  = st.selectbox("売買", ["買い", "売り"])
        t_price = st.number_input("単価（円）", min_value=0, value=0, step=1)
    with col_c:
        t_qty   = st.number_input("枚数", min_value=0, value=0, step=1)
        t_memo  = st.text_input("メモ（任意）", key="trade_memo")

    if st.button("売買を記録"):
        if t_code and t_price > 0 and t_qty > 0:
            code_n = normalize_code(t_code)
            name_n, *_ = fetch_stock_data(code_n)
            amount = t_price * t_qty
            new_trade = pd.DataFrame([{
                "日付":   t_date.isoformat(),
                "コード": code_n,
                "銘柄名": name_n or t_code,
                "売買":   t_type,
                "単価":   t_price,
                "枚数":   t_qty,
                "金額":   amount,
                "メモ":   t_memo
            }])
            trade_df = pd.concat([trade_df, new_trade], ignore_index=True)
            save_df(trade_sheet, trade_df)

            # ----------
            # 保有株を自動更新
            # ----------
            holding_df = load_df(holding_sheet, HOLDING_COLS)
            if "コード" in holding_df.columns:
                holding_df["コード"] = holding_df["コード"].apply(normalize_code)
            for col in ["取得単価", "枚数"]:
                if col in holding_df.columns:
                    holding_df[col] = pd.to_numeric(holding_df[col], errors="coerce")

            # 該当銘柄を探す
            existing = holding_df[holding_df["コード"] == code_n]

            if t_type == "買い":
                if len(existing) > 0:
                    # 既存銘柄：加重平均で取得単価を更新
                    idx = existing.index[0]
                    old_qty   = float(holding_df.loc[idx, "枚数"])
                    old_cost  = float(holding_df.loc[idx, "取得単価"])
                    new_qty   = old_qty + t_qty
                    new_cost  = (old_cost * old_qty + t_price * t_qty) / new_qty
                    holding_df.loc[idx, "枚数"] = new_qty
                    holding_df.loc[idx, "取得単価"] = round(new_cost, 0)
                else:
                    # 新規銘柄
                    holding_df = pd.concat([holding_df, pd.DataFrame([{
                        "コード": code_n,
                        "銘柄名": name_n or t_code,
                        "取得単価": t_price,
                        "枚数": t_qty
                    }])], ignore_index=True)
            else:  # 売り
                if len(existing) > 0:
                    idx = existing.index[0]
                    old_qty = float(holding_df.loc[idx, "枚数"])
                    new_qty = old_qty - t_qty
                    if new_qty > 0:
                        holding_df.loc[idx, "枚数"] = new_qty
                    else:
                        # 全株売却
                        holding_df = holding_df.drop(idx)

            save_df(holding_sheet, holding_df)

            # ----------
            # 現金を自動更新
            # ----------
            cash_df = load_df(cash_sheet, CASH_COLS)
            current_cash = pd.to_numeric(cash_df.iloc[0]["現金残高"], errors="coerce") if len(cash_df) > 0 else 0
            if t_type == "買い":
                new_cash = current_cash - amount
            else:  # 売り
                new_cash = current_cash + amount
            save_df(cash_sheet, pd.DataFrame([{"現金残高": new_cash}]))

            st.success(f"{t_type}を記録し、保有株・現金を更新しました（{code_n} {t_qty}枚 @{t_price:,}円）")
            st.rerun()
        else:
            st.warning("銘柄コード・単価・枚数を入力してください")

    st.divider()

    # ----------
    # 履歴一覧
    # ----------
    st.subheader("📒 売買履歴一覧")

    if len(trade_df) > 0:
        for col in ["単価", "枚数", "金額"]:
            if col in trade_df.columns:
                trade_df[col] = pd.to_numeric(trade_df[col], errors="coerce")

        # 銘柄フィルター
        codes = ["すべて"] + sorted(trade_df["コード"].unique().tolist())
        filter_code = st.selectbox("銘柄で絞り込み", codes)
        if filter_code != "すべて":
            show_df = trade_df[trade_df["コード"] == filter_code]
        else:
            show_df = trade_df

        show_df = show_df.sort_values("日付", ascending=False)

        st.dataframe(
            show_df,
            use_container_width=True,
            column_config={
                "単価": st.column_config.NumberColumn(format="¥%.0f"),
                "金額": st.column_config.NumberColumn(format="¥%.0f"),
            }
        )

        # ----------
        # 銘柄別損益集計
        # ----------
        st.divider()
        st.subheader("📊 銘柄別 実現損益")

        buy_df  = trade_df[trade_df["売買"] == "買い"].copy()
        sell_df = trade_df[trade_df["売買"] == "売り"].copy()

        summary_rows = []
        for code in trade_df["コード"].unique():
            b = buy_df[buy_df["コード"] == code]
            s = sell_df[sell_df["コード"] == code]
            buy_amount  = (b["単価"] * b["枚数"]).sum()
            buy_qty     = b["枚数"].sum()
            sell_amount = (s["単価"] * s["枚数"]).sum()
            sell_qty    = s["枚数"].sum()
            avg_cost    = buy_amount / buy_qty if buy_qty > 0 else 0
            realized    = sell_amount - (avg_cost * sell_qty) if sell_qty > 0 else 0
            name        = trade_df[trade_df["コード"] == code]["銘柄名"].iloc[0]
            summary_rows.append({
                "コード":     code,
                "銘柄名":     name,
                "買い枚数":   int(buy_qty),
                "売り枚数":   int(sell_qty),
                "保有枚数":   int(buy_qty - sell_qty),
                "平均取得単価": round(avg_cost, 0),
                "実現損益":   round(realized, 0),
            })

        summary_df = pd.DataFrame(summary_rows)
        st.dataframe(
            summary_df,
            use_container_width=True,
            column_config={
                "平均取得単価": st.column_config.NumberColumn(format="¥%.0f"),
                "実現損益":     st.column_config.NumberColumn(format="¥%.0f"),
            }
        )
    else:
        st.info("まだ売買履歴がありません")
