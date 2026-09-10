# -*- coding: utf-8 -*-
"""
7729 券商分點每日明細 — TPEx 興櫃買賣日報表（EMdss004）

資料源（Marvin 2026-07-24 來信提供，2026-07-27 實測可程式化取得）：
  https://www.tpex.org.tw/www/zh-tw/emerging/dailyDl?name=EMdss004.YYYYMMDD-C.csv

⚠️ 三個會卡住的地方：
  1. **必須帶 /www/ 前綴**。頁面上列出的連結是 /zh-tw/emerging/dailyDl?...，
     直接打會 302 到 /errors；前面加 /www 才拿得到檔案。
  2. 編碼是 **Big5**（big5hkscs 才不會有罕用字掉字），換行 CRLF。
  3. 檔案格式是自訂的 TITLE/HEADER/BODY 前綴，不是標準 CSV 表頭。

格式：BODY,證券代號,證券名稱,證券商代號,成交價,買進股數,賣出股數
     → 一列 = 一天 × 一檔股票 × 一分點 × 一個成交價（同分點同日多價位會有多列）

⚠️ 只涵蓋「電腦議價點選系統交易」，不含系統外議價（那是 EMdcs002）。
   現行《生技籌碼與市值綜合日報》也只用這一份，兩邊才對得起來 —— 不要自作主張合併。

輸出 data/broker_daily.json：一列 = 一天 × 一分點，**不預先加總 5／10 日**。
聚合交給前端，任意區間才成立（這正是取代 email 報表的關鍵）。

原始 CSV 每天 2MB 且含全市場，不留存；只把 7729 的列存進 cache/（一天一檔、約 6KB，
進版控），重跑時不必重新下載，排程機器換人跑也不必重新回補一整年。

⚠️ 輸出一律涵蓋 cache 裡的所有交易日，不是只有這次抓取區間 ——
   否則每天跑 --days 7 會把一整年的歷史蓋成只剩 7 天。

用法：
  python fetch_broker_daily.py            # 回補最近 365 天
  python fetch_broker_daily.py --days 30
  python fetch_broker_daily.py 2026-07-01 2026-07-25
"""
import csv, io, json, os, sys, time, urllib.error
from datetime import date, datetime, timedelta, timezone

from _http import fetch_bytes

STOCK = "7729"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, "cache", STOCK)
NODATA = os.path.join(ROOT, "cache", "nodata.json")

URL = "https://www.tpex.org.tw/www/zh-tw/emerging/dailyDl?name=EMdss004.{ymd}-C.csv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://www.tpex.org.tw/zh-tw/esb/trading/info/historical/day.html",
}

# 「今天」一律用台北時間。GitHub Actions 的機器是 UTC，排程若被延遲到台北午夜之後，
# date.today() 會還停在前一天；這裡的日期判斷全靠它，不能交給機器時區決定。
TPE = timezone(timedelta(hours=8))

# ── 券商改碼對照（兩件都有官方來源，是事實不是用資料反推的猜測）──
#   元富 592x → 9B2x：2026-02-02 起，「各分公司代號由原 592* 改為 9B2*」，後一碼不變。
#     元富證券公告（115-01-05）https://www.masterlink.com.tw/936/
#   台新 815x → 9B1x：台新與元富合併基準日 2026-04-06，台新自家分公司自 04-07 改用 9B1x，
#     後一碼**有變**，要靠分公司名稱對應。台新證券合併專區「服務據點異動」列出對照
#     （8150→9B17 營業部、8151→9B18 建北、8152→9B19 新莊、8156→9B13 三民、
#       8159→9B16 台南、815A→9B1g 高雄、815B→9B1h 台中），其餘依 TWSE 現行分公司名稱補齊。
#     https://www.tssco.com.tw/TSHOLDINGSMERGE/locations/index.html
# 不合併的話，區間一跨過改碼日，同一家分點就被拆成新舊兩列，長區間累計會低估
# （例：8150 台新 +26.8 萬 與 9B17 台新-台北營業部 +22.5 萬其實是同一家）。
# 這裡只產出對照表、不改寫每日明細；合併由前端載入時做，cache 與對帳腳本都不受影響。
ALIASES = {
    "8150": "9B17", "8151": "9B18", "8152": "9B19", "8156": "9B13",
    "8157": "9B1d", "8158": "9B15", "8159": "9B16", "815A": "9B1g",
    "815B": "9B1h", "815H": "9B1n", "815S": "9B1y", "815Y": "9B11",
}
ALIAS_EVENTS = [
    {"date": "2026-02-02", "from": "592x", "to": "9B2x", "firm": "元富證券（後併入台新）",
     "rule": "後一碼不變", "source": "https://www.masterlink.com.tw/936/"},
    {"date": "2026-04-07", "from": "815x", "to": "9B1x", "firm": "台新證券",
     "rule": "依分公司名稱對應", "source": "https://www.tssco.com.tw/TSHOLDINGSMERGE/locations/index.html"},
]


def alias_of(code):
    if code in ALIASES:
        return ALIASES[code]
    if len(code) == 4 and code.startswith("592"):
        return "9B2" + code[3:]
    return None


# 「查無資料」要多久之後才可信。TPEx 約 16:35 出檔；在那之前打「今天」一定是空的，
# 那不是非交易日，只是還沒出。10 天內的空結果一律不當定論、下次再查一次。
NODATA_TRUST_AFTER_DAYS = 10


def today_tpe():
    return datetime.now(TPE).date()


def num(s, default=0):
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "--"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def parse(raw):
    """全市場 CSV → 只留 STOCK 的分點彙總 [[code, buy, sell, amount], ...]

    amount = Σ 成交價 × (買進股數 + 賣出股數)，用來還原成交量加權均價。
    存 amount 而非均價，多日聚合才能精確加總（存均價再平均會錯）。
    """
    txt = raw.decode("big5hkscs", errors="replace")
    agg = {}
    for r in csv.reader(io.StringIO(txt)):
        if len(r) < 7 or r[0] != "BODY" or r[1].strip() != STOCK:
            continue
        code = r[3].strip()
        price, buy, sell = num(r[4]), int(num(r[5])), int(num(r[6]))
        a = agg.setdefault(code, [code, 0, 0, 0.0])
        a[1] += buy
        a[2] += sell
        a[3] += price * (buy + sell)
    rows = sorted(agg.values(), key=lambda x: -(x[1] - x[2]))
    return [[c, b, s, round(amt, 2)] for c, b, s, amt in rows]


def fetch_day(d, nodata, today):
    """回傳當日分點列；無交易日回傳 None。已抓過的直接讀 cache。

    ⚠️ 「查無資料」不等於「非交易日」。2026-07-27、07-31、09-10 三天都是正常交易日，
    卻因為在盤後檔案出來之前跑了一次（手動測試、或排程被提前）而被永久標成 nodata，
    之後每天的 --days 7 都跳過它們，網站上那幾天的分點就一直是空的。
    所以：當天（台北時間）的空結果**不寫入** nodata；main() 載入時也會把 10 天內的
    標記丟掉重查。多打幾個請求換資料不會無聲消失，划算。
    """
    ymd = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, f"{ymd}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if ymd in nodata:
        return None

    try:
        raw = fetch_bytes(URL.format(ymd=ymd), HEADERS, timeout=90)
    except urllib.error.HTTPError as e:
        raw = b"" if e.code in (302, 404) else None
        if raw is None:
            raise
    # 非交易日會 302 到 /errors，urllib 跟隨後拿到 HTML；用長度與內容判斷
    if len(raw) < 5000 or b"BODY" not in raw[:200000]:
        if d < today:
            nodata.add(ymd)
        return None

    rows = parse(raw)
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return rows


def load_lut():
    path = os.path.join(DATA, "broker_lut.json")
    if not os.path.exists(path):
        sys.exit("找不到 data/broker_lut.json，請先執行 python fetch_broker_lut.py")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    args = sys.argv[1:]
    today = today_tpe()
    if len(args) == 2 and not args[0].startswith("--"):
        d0 = date.fromisoformat(args[0])
        d1 = date.fromisoformat(args[1])
    else:
        days = 365
        if args and args[0] == "--days":
            days = int(args[1])
        d0, d1 = today - timedelta(days=days), today

    nodata = set()
    if os.path.exists(NODATA):
        with open(NODATA, encoding="utf-8") as f:
            nodata = set(json.load(f))
    # 近 10 天的「查無資料」不算數，重查（見 fetch_day 的說明）
    trust_before = (today - timedelta(days=NODATA_TRUST_AFTER_DAYS)).strftime("%Y%m%d")
    nodata = {x for x in nodata if x < trust_before}

    lut = load_lut()
    names, makers = lut["names"], set(lut["market_makers"])

    fetched = 0
    d = d0
    while d <= d1:
        if d.weekday() < 5:  # 六日直接跳過，少打兩百多次無謂的請求
            hit = os.path.exists(os.path.join(CACHE, d.strftime("%Y%m%d.json")))
            try:
                rows = fetch_day(d, nodata, today)
            except Exception as e:
                print(f"  {d} FAIL {type(e).__name__}: {e}")
                d += timedelta(days=1)
                continue
            if rows and not hit:
                fetched += 1
                print(f"  {d} {len(rows):>3} 分點")
                time.sleep(0.5)
            elif not rows and not hit:
                time.sleep(0.3)
        d += timedelta(days=1)

    os.makedirs(os.path.dirname(NODATA), exist_ok=True)
    with open(NODATA, "w", encoding="utf-8") as f:
        json.dump(sorted(nodata), f)

    # ⚠️ 輸出一律涵蓋 cache 裡的**所有**交易日，不受這次抓取區間影響。
    # 否則每天跑 --days 7 會把一整年的歷史蓋成只剩 7 天。
    daily = {}
    for fn in sorted(os.listdir(CACHE)) if os.path.isdir(CACHE) else []:
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
            rows = json.load(f)
        if rows:
            daily[f"{fn[0:4]}-{fn[4:6]}-{fn[6:8]}"] = rows

    if not daily:
        sys.exit("沒有抓到任何資料")

    used = sorted({r[0] for rows in daily.values() for r in rows})
    # 舊碼一律掛到新碼的名稱下（見 ALIASES 的說明）；新碼若只在舊碼期間出現過也要有名字
    aliases = {c: alias_of(c) for c in used if alias_of(c)}
    # 查無名稱多半是已停業／併購的券商，只會出現在較早的歷史資料裡。
    # 直接顯示代號並標記，不要無聲當成一家沒名字的分點。
    unknown = [c for c in used if c not in names and c not in aliases]
    brokers = {}
    for c in used:
        new = aliases.get(c)
        b = {"n": names.get(new or c, c), "mm": c in makers}
        if new:
            b["alias"] = new
            b["old_name"] = names.get(c) or ("元富" + names.get(new, "")[2:] if names.get(new, "").startswith("台新") else c)
        elif c not in names:
            b["unk"] = True
        brokers[c] = b
    for new in set(aliases.values()):
        brokers.setdefault(new, {"n": names.get(new, new), "mm": new in makers})

    dates = sorted(daily)
    out = {
        "stock_id": STOCK,
        "generated_at": today.isoformat(),
        "source": "TPEx 興櫃買賣日報表 EMdss004（電腦議價點選系統交易）",
        "source_url": "https://www.tpex.org.tw/zh-tw/esb/trading/info/historical/day.html",
        "schema": "daily[日期] = [[券商代號, 買進股數, 賣出股數, 成交金額], ...]；"
                  "淨額＝買進－賣出，均價＝成交金額÷(買進＋賣出)",
        "note": "每日明細，未預先加總；任意區間由前端聚合",
        "date_from": dates[0],
        "date_to": dates[-1],
        "trading_days": len(dates),
        "dates": dates,
        "brokers": brokers,
        "aliases": aliases,
        "alias_events": ALIAS_EVENTS,
        "alias_note": "舊券商代號 → 現行代號。每日明細保留原始代號，前端載入時依此表併為同一分點。",
        "daily": {d: daily[d] for d in dates},
    }
    with open(os.path.join(DATA, "broker_daily.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(os.path.join(DATA, "broker_daily.json")) / 1024
    print(f"\n交易日 {len(dates)} 天：{dates[0]} ~ {dates[-1]}（本次新抓 {fetched} 天）")
    print(f"分點 {len(used)} 家，broker_daily.json {size:.0f} KB")
    print(f"造市商：{'、'.join(f'{c} {names.get(c, c)}' for c in sorted(makers))}")
    print(f"改碼對照 {len(aliases)} 個舊碼（元富 592x→9B2x、台新 815x→9B1x），前端載入時合併")
    if unknown:
        print(f"⚠️ {len(unknown)} 個代號查無名稱，請補進 fetch_broker_lut.py 的 EXTRA："
              f"{'、'.join(unknown)}")


if __name__ == "__main__":
    main()
