import streamlit as st
import yfinance as yf
import pandas as pd
import time
import json
import gspread
import requests
import io
from google.oauth2.service_account import Credentials
from yfinance.exceptions import YFRateLimitError

# --------------------
# 初期設定
# --------------------
st.set_page_config(layout="wide")
st.title("📈 株式銘柄管理ツール")

SPREADSHEET_ID = "1noyNkmaeisqi96_xAFS-yo18pqtcWOu8yOpDzzOKnhg"
SHEET_NAME = "stocks"

COLUMNS = ["コード", "銘柄名", "株価", "PER", "PBR", "ROE", "配当",
           "四季報", "タグ", "メモ", "目標株価", "削除"]

# --------------------
# 東証銘柄名マスタ
# --------------------
@st.cache_data(ttl=86400)  # 1日キャッシュ
def load_tse_master():
    """東証の銘柄一覧から証券コード→日本語銘柄名の辞書を返す"""
    try:
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        r = requests.get(url, timeout=10)
        xls = pd.read_excel(io.BytesIO(r.content), header=0)
        # 列名を確認して証券コードと銘柄名を取得
        code_col = [c for c in xls.columns if "コード" in str(c)][0]
        name_col = [c for c in xls.columns if "銘柄名" in str(c)][0]
        master = dict(zip(xls[code_col].astype(str).str.zfill(4), xls[name_col]))
        return master
    except Exception:
        return {}

# --------------------
# Google Sheets接続
# --------------------
def get_sheet():
    creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    try:
        sheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=20)
        sheet.append_row(COLUMNS)
    return sheet

def load_df(sheet):
    values = sheet.get_all_values()
    if len(values) <= 1:
        return pd.DataFrame(columns=COLUMNS)
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    # 削除列をbool型に変換
    if "削除" in df.columns:
        df["削除"] = df["削除"].apply(lambda x: str(x).upper() == "TRUE")
    return df

def save_df(sheet, df):
    save = df.copy()
    # NaN・None をすべて空文字に変換
    save = save.fillna("")
    # float型の整数値（1.0など）を整数に変換
    for col in save.columns:
        if col != "削除":
            save[col] = save[col].apply(
                lambda x: int(x) if isinstance(x, float) and not pd.isna(x) and x == int(x) else x
            )
    # 削除列はTRUE/FALSEの文字列で保存
    save["削除"] = save["削除"].apply(lambda x: "TRUE" if x is True or str(x).upper() == "TRUE" else "FALSE")
    sheet.clear()
    sheet.update([save.columns.tolist()] + save.values.tolist())

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

        # 日本株は東証マスタから日本語名を取得
        if code.endswith(".T"):
            raw = code.replace(".T", "").zfill(4)
            master = load_tse_master()
            jp_name = master.get(raw, "")
            if jp_name:
                name = jp_name

        price = info.get("currentPrice")
        per = info.get("trailingPE")
        pbr = info.get("priceToBook")
        roe = info.get("returnOnEquity")
        if roe is not None:
            roe *= 100
        div = info.get("dividendYield")

        return name, price, per, pbr, roe, div

    except YFRateLimitError:
        return "", None, None, None, None, None
    except Exception:
        return "", None, None, None, None, None


def normalize_tags(tag_str):
    if not isinstance(tag_str, str):
        return ""
    tags = tag_str.replace("　", " ").strip()
    tags = " ".join(tags.split())
    return tags


def normalize_code(code):
    code = str(code).strip().upper()
    if "." in code:
        return code
    if len(code) <= 5:
        return f"{code}.T"
    return code


def get_ir_links(code):
    raw = code.upper().replace(".T", "").strip()
    ir_searcher = f"https://ir-searcher.com/kobetsu.php?code={raw}"
    irbank = f"https://irbank.net/{raw}"
    return ir_searcher, irbank


def format_for_display(df):
    view = df.copy()
    for col in ["株価", "PER", "PBR", "ROE", "配当"]:
        if col in view.columns:
            view[col] = pd.to_numeric(view[col], errors="coerce")
    view["株価"] = view["株価"].round(0)
    view["PER"] = view["PER"].round(1)
    view["PBR"] = view["PBR"].round(1)
    view["ROE(%)"] = view["ROE"].round(1)
    if "配当" in view.columns:
        view["配当"] = view["配当"].round(2)
    view["IR Searcher"] = view["コード"].apply(lambda c: get_ir_links(c)[0])
    view["irbank"] = view["コード"].apply(lambda c: get_ir_links(c)[1])
    view.drop(columns=["ROE"], inplace=True)
    col_order = [
        "コード", "銘柄名", "株価", "PER", "PBR", "ROE(%)", "配当",
        "四季報", "目標株価", "タグ", "メモ", "IR Searcher", "irbank", "削除"
    ]
    view = view[[c for c in col_order if c in view.columns]]
    return view


def get_all_tags(df):
    tags = set()
    for t in df["タグ"]:
        if isinstance(t, str):
            tags.update(t.split())
    return sorted(tags)


# --------------------
# データ読み込み
# --------------------
sheet = get_sheet()
df = load_df(sheet)

# 列の保険 & 型補正
defaults = {
    "銘柄名": "",
    "四季報": 0,
    "配当": None,
    "タグ": "",
    "メモ": "",
    "目標株価": None,
    "削除": False
}
for col, default in defaults.items():
    if col not in df.columns:
        df[col] = default

for col in ["タグ", "メモ", "銘柄名"]:
    df[col] = df[col].astype(str).fillna("")

# 銘柄名が空の行は取得を試みる
for i, row in df.iterrows():
    if not row["銘柄名"] or row["銘柄名"] in ("", "nan"):
        time.sleep(1)
        name, *_ = fetch_stock_data(row["コード"])
        if name:
            df.loc[i, "銘柄名"] = name

# --------------------
# 銘柄追加（手入力）
# --------------------
st.subheader("➕ 銘柄を追加")

raw_code = st.text_input("銘柄コード（例：7203 / AAPL）")
code = normalize_code(raw_code)

if st.button("銘柄を追加"):
    name, price, per, pbr, roe, div = fetch_stock_data(code)

    if code in df["コード"].values:
        df.loc[df["コード"] == code, "四季報"] += 1
        if name:
            df.loc[df["コード"] == code, "銘柄名"] = name
        st.info("既存銘柄のため、四季報を +1 しました")
    else:
        new_row = {
            "コード": code, "銘柄名": name, "株価": price,
            "PER": per, "PBR": pbr, "ROE": roe, "配当": div,
            "四季報": 1, "タグ": "", "メモ": "", "目標株価": None, "削除": False
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        st.success("新しい銘柄を追加しました")

    save_df(sheet, df)
    st.rerun()

st.divider()

# --------------------
# CSV追加
# --------------------
st.subheader("📂 CSVから銘柄を追加")
uploaded_file = st.file_uploader("code列を持つCSV", type="csv")

if uploaded_file:
    add_df = pd.read_csv(uploaded_file)
    if "code" not in add_df.columns and "コード" not in add_df.columns:
        st.error("CSVに code 列または コード 列がありません")
    else:
        code_col = "code" if "code" in add_df.columns else "コード"
        for raw_code in add_df[code_col]:
            code = normalize_code(raw_code)
            if code not in df["コード"].values:
                name, price, per, pbr, roe, div = fetch_stock_data(code)
                df = pd.concat([df, pd.DataFrame([{
                    "コード": code, "銘柄名": name, "株価": price,
                    "PER": per, "PBR": pbr, "ROE": roe, "配当": div,
                    "四季報": 1, "タグ": "", "メモ": "", "目標株価": None, "削除": False
                }])], ignore_index=True)
        save_df(sheet, df)
        st.success("CSV追加完了")

st.divider()

# --------------------
# 表示・編集
# --------------------
st.subheader("📊 登録銘柄一覧")

sort_col = st.selectbox("並び替え", ["株価", "PER", "PBR", "ROE(%)", "配当"])
ascending = st.checkbox("昇順", False)

for col in ["株価", "PER", "PBR", "ROE", "配当"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

df = df.sort_values(
    by="ROE" if sort_col == "ROE(%)" else sort_col,
    ascending=ascending,
    na_position="last"
)

view_df = format_for_display(df)

edited_df = st.data_editor(
    view_df,
    use_container_width=True,
    column_config={
        "タグ": st.column_config.TextColumn(
            help="スペース区切りで複数指定（例：高配当 長期）"
        ),
        "メモ": st.column_config.TextColumn(width="large"),
        "IR Searcher": st.column_config.LinkColumn(
            display_text="🔍 IR Searcher",
            disabled=True,
        ),
        "irbank": st.column_config.LinkColumn(
            display_text="📊 irbank",
            disabled=True,
        ),
    }
)

# 内部列へ戻す
edited_df["ROE"] = edited_df["ROE(%)"]
edited_df.drop(columns=["ROE(%)", "IR Searcher", "irbank"], inplace=True)

st.subheader("🏷️ タグで絞り込み")

all_tags = get_all_tags(df)
selected_tags = st.multiselect("タグを選択", all_tags)

if selected_tags:
    df = df[df["タグ"].apply(
        lambda x: all(tag in x.split() for tag in selected_tags)
    )]

# --------------------
# 操作ボタン
# --------------------
if st.button("編集内容を保存"):
    df["タグ"] = df["タグ"].apply(normalize_tags)
    df = edited_df.copy()
    save_df(sheet, df)
    st.success("保存しました")
    st.rerun()

if st.button("選択した銘柄を削除"):
    df = edited_df[edited_df["削除"] != True]
    df["削除"] = False
    save_df(sheet, df)
    st.success("削除しました")
    st.rerun()

if st.button("全銘柄を更新"):
    for i, row in df.iterrows():
        name, price, per, pbr, roe, div = fetch_stock_data(row["コード"])
        if name:
            df.loc[i, "銘柄名"] = name
        df.loc[i, ["株価", "PER", "PBR", "ROE", "配当"]] = [price, per, pbr, roe, div]
    save_df(sheet, df)
    st.success("全銘柄を更新しました")

if st.button("銘柄名を日本語に更新"):
    master = load_tse_master()
    for i, row in df.iterrows():
        code = row["コード"]
        if code.endswith(".T"):
            raw = code.replace(".T", "").zfill(4)
            jp_name = master.get(raw, "")
            if jp_name:
                df.loc[i, "銘柄名"] = jp_name
    save_df(sheet, df)
    st.success("日本語銘柄名に更新しました")
    st.rerun()
