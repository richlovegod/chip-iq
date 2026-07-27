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
import csv, io, json, os, sys, time, urllib.error, urllib.request
from datetime import date, timedelta

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


def fetch_day(d, nodata):
    """回傳當日分點列；無交易日回傳 None。已抓過的直接讀 cache。"""
    ymd = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, f"{ymd}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if ymd in nodata:
        return None

    req = urllib.request.Request(URL.format(ymd=ymd), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = b"" if e.code in (302, 404) else None
        if raw is None:
            raise
    # 非交易日會 302 到 /errors，urllib 跟隨後拿到 HTML；用長度與內容判斷
    if len(raw) < 5000 or b"BODY" not in raw[:200000]:
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
    today = date.today()
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

    lut = load_lut()
    names, makers = lut["names"], set(lut["market_makers"])

    fetched = 0
    d = d0
    while d <= d1:
        if d.weekday() < 5:  # 六日直接跳過，少打兩百多次無謂的請求
            hit = os.path.exists(os.path.join(CACHE, d.strftime("%Y%m%d.json")))
            try:
                rows = fetch_day(d, nodata)
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
    # 查無名稱多半是已停業／併購的券商，只會出現在較早的歷史資料裡。
    # 直接顯示代號並標記，不要無聲當成一家沒名字的分點。
    unknown = [c for c in used if c not in names]
    brokers = {}
    for c in used:
        b = {"n": names.get(c, c), "mm": c in makers}
        if c not in names:
            b["unk"] = True
        brokers[c] = b

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
        "daily": {d: daily[d] for d in dates},
    }
    with open(os.path.join(DATA, "broker_daily.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(os.path.join(DATA, "broker_daily.json")) / 1024
    print(f"\n交易日 {len(dates)} 天：{dates[0]} ~ {dates[-1]}（本次新抓 {fetched} 天）")
    print(f"分點 {len(used)} 家，broker_daily.json {size:.0f} KB")
    print(f"造市商：{'、'.join(f'{c} {names.get(c, c)}' for c in sorted(makers))}")
    if unknown:
        print(f"⚠️ {len(unknown)} 個代號查無名稱，請補進 fetch_broker_lut.py 的 EXTRA："
              f"{'、'.join(unknown)}")


if __name__ == "__main__":
    main()
