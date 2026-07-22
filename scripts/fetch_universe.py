# -*- coding: utf-8 -*-
"""
生技醫療全市場市值排名 — 7729 大盤排名 + Top 30

對應現行報表第三塊「市值戰報」的焦點個股排名與大盤排名總表。

範圍：三個市場中產業別代碼為 22（生技醫療）的全部公司
      （上市 t187ap03_L 的「產業別」／上櫃與興櫃的 SecuritiesIndustryCode）

取價：上市、上櫃取收盤價；興櫃取成交均價。當日無成交者沿用最近一個交易日的價格
      （否則興櫃冷門股會憑空從排名中消失）。

請求量：上市與上櫃各有全市場單日批次端點，各 1 請求即可；
       興櫃沒有批次端點，只能逐檔抓，但每次回傳整個月，故一檔一請求就夠。

用法：python fetch_universe.py [YYYY-MM-DD]   預設抓最近交易日
"""
import json, os, sys, time, re, urllib.request
from datetime import date, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
FOCUS = "7729"
BIOTECH = "22"          # 產業別：生技醫療
TOP_N = 30

TW = "https://www.twse.com.tw/"
TP = "https://www.tpex.org.tw/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/126.0 Safari/537.36")

# 現行報表 2026-07-21 那期 → 回歸測試黃金樣本
GOLD_DATE = "2026-07-21"
GOLD_FOCUS = {"rank": 67, "prev_rank": 68, "cap": 56.89, "prev_cap": 55.69}
GOLD_TOP30 = ["6949", "6696", "6446", "6919", "7799", "6472", "1795", "4169",
              "4123", "6586", "6491", "4743", "4728", "4105", "7827", "4147",
              "1789", "6547", "4114", "6712", "3705", "6535", "6620", "6748",
              "6589", "8436", "1707", "4104", "6617", "6576"]


def get(url, referer):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    return json.load(urllib.request.urlopen(req, timeout=60))


def num(s):
    s = re.sub(r"<[^>]+>", "", str(s)).replace(",", "").strip()
    if s in ("", "-", "--", "---"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    y, m, d = s.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def load_universe():
    """三個市場的生技醫療公司：代號 → (市場, 簡稱, 已發行普通股數)"""
    out = {}
    for r in get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", TW):
        if r.get("產業別") == BIOTECH:
            out[r["公司代號"]] = ("上市", r["公司簡稱"],
                                  num(r["已發行普通股數或TDR原股發行股數"]))
    for path, mkt in (("mopsfin_t187ap03_O", "上櫃"), ("mopsfin_t187ap03_R", "興櫃")):
        for r in get(TP + "openapi/v1/" + path, TP):
            if r.get("SecuritiesIndustryCode") == BIOTECH:
                out.setdefault(r["SecuritiesCompanyCode"],
                               (mkt, r["CompanyAbbreviation"], num(r["IssueShares"])))
    return out


def px_listed_bulk(d):
    """上市全市場單日收盤（欄位含 ETF／權證，靠代號比對過濾）"""
    j = get(TW + f"rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALL&response=json", TW)
    for t in j.get("tables", []):
        if t.get("fields") and t["fields"][0] == "證券代號":
            return {r[0]: num(r[8]) for r in t["data"] if num(r[8])}
    return {}


def px_otc_bulk(d):
    """上櫃全市場單日收盤。⚠️ type=EW 必填，沒帶會回傳 0 筆而不是報錯"""
    roc = f"{d.year - 1911}/{d:%m/%d}"
    j = get(TP + f"www/zh-tw/afterTrading/otc?date={roc}&type=EW&response=json", TP)
    if not j.get("tables") or not j["tables"][0].get("data"):
        return {}
    return {r[0]: num(r[2]) for r in j["tables"][0]["data"] if num(r[2])}


def px_esb_month(code, y, m):
    """興櫃單檔整月成交均價（欄位 5）"""
    j = get(TP + f"www/zh-tw/emerging/historical?date={y}/{m:02d}/01"
                 f"&code={code}&response=json", TP)
    if j.get("stat") != "ok" or not j.get("tables"):
        return {}
    return {roc_to_iso(r[0]): num(r[5])
            for r in j["tables"][0]["data"] if num(r[1]) and num(r[5])}


def prev_month(y, m):
    return (y - 1, 12) if m == 1 else (y, m - 1)


def find_trading_days(target):
    """回傳 (當日, 前一交易日)。以上市批次資料有無回傳判斷是否為交易日。"""
    days, d, tries = [], target, 0
    while len(days) < 2 and tries < 14:
        if px_listed_bulk(d):
            days.append(d)
        d -= timedelta(days=1)
        tries += 1
        time.sleep(0.3)
    if len(days) < 2:
        raise SystemExit("找不到連續兩個交易日，請確認日期或連線")
    return days[0], days[1]


def main():
    target = (date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today())
    uni = load_universe()
    print(f"生技醫療（產業別 {BIOTECH}）共 {len(uni)} 家："
          f"上市 {sum(1 for v in uni.values() if v[0] == '上市')}／"
          f"上櫃 {sum(1 for v in uni.values() if v[0] == '上櫃')}／"
          f"興櫃 {sum(1 for v in uni.values() if v[0] == '興櫃')}")

    d1, d0 = find_trading_days(target)      # d1=當日, d0=前一交易日
    print(f"當日 {d1}　前一交易日 {d0}")

    prices = {}     # code -> {iso_date: price}
    for d in (d1, d0):
        for src in (px_listed_bulk, px_otc_bulk):
            for code, p in src(d).items():
                if code in uni:
                    prices.setdefault(code, {})[d.isoformat()] = p
            time.sleep(0.3)

    esb = [c for c, v in uni.items() if v[0] == "興櫃"]
    months = {(d1.year, d1.month), (d0.year, d0.month)}
    for i, code in enumerate(esb, 1):
        got = {}
        for y, m in sorted(months):
            try:
                got.update(px_esb_month(code, y, m))
            except Exception:
                pass
            time.sleep(0.3)
        if not got:      # 整月零成交，往前一個月再找一次
            y, m = prev_month(*min(months))
            try:
                got.update(px_esb_month(code, y, m))
            except Exception:
                pass
            time.sleep(0.3)
        if got:
            prices.setdefault(code, {}).update(got)
        if i % 20 == 0:
            print(f"  興櫃 {i}/{len(esb)}")

    def cap_on(code, d):
        """當日無成交則沿用最近一個交易日的價格"""
        series = prices.get(code) or {}
        avail = [k for k in series if k <= d.isoformat()]
        if not avail:
            return None
        shares = uni[code][2]
        return round(series[max(avail)] * shares / 1e8, 2) if shares else None

    rows = []
    for code, (mkt, name, shares) in uni.items():
        c1, c0 = cap_on(code, d1), cap_on(code, d0)
        if c1 is None:
            continue
        rows.append({"code": code, "name": name, "market": mkt,
                     "shares": int(shares), "cap": c1, "prev_cap": c0})

    rows.sort(key=lambda r: -r["cap"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    prev_rank = {r["code"]: i for i, r in enumerate(
        sorted([r for r in rows if r["prev_cap"] is not None],
               key=lambda r: -r["prev_cap"]), 1)}
    for r in rows:
        r["prev_rank"] = prev_rank.get(r["code"])
        r["rank_change"] = (r["prev_rank"] - r["rank"]) if r["prev_rank"] else None

    focus = next((r for r in rows if r["code"] == FOCUS), None)
    missing = len(uni) - len(rows)

    out = {
        "universe": "生技醫療（產業別 22）",
        "as_of": d1.isoformat(),
        "prev_date": d0.isoformat(),
        "updated_at": date.today().isoformat(),
        "focus": focus,
        "total": len(rows),
        "no_price": missing,
        "method": {
            "scope": "上市＋上櫃＋興櫃，產業別代碼 22",
            "price": "上市/上櫃收盤價、興櫃成交均價；當日無成交沿用最近交易日",
            "shares": "MOPS 已發行普通股數，每次執行重抓",
        },
        "top": rows[:TOP_N],
    }
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "universe.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n納入排名 {len(rows)} 家（{missing} 家查無價格）")
    if focus:
        ch = focus["rank_change"]
        arrow = "—" if not ch else ("▲" + str(ch) if ch > 0 else "▼" + str(-ch))
        print(f"{FOCUS} 排名 第 {focus['rank']} 名（{arrow}）　"
              f"市值 {focus['cap']} 億（前日 {focus['prev_cap']}）")

    print(f"\nTop {TOP_N}：")
    for r in out["top"]:
        print(f"  {r['rank']:>2} {r['code']} {r['name']:<11}{r['market']:<5}"
              f"{r['cap']:>10.2f}{(r['prev_cap'] or 0):>10.2f}")

    if d1.isoformat() == GOLD_DATE:
        print(f"\n=== 回歸測試 vs 現行報表 {GOLD_DATE} ===")
        got = [r["code"] for r in out["top"]]
        same = sum(1 for a, b in zip(got, GOLD_TOP30) if a == b)
        print(f"Top 30 名次完全相同：{same}/30；成分重疊：{len(set(got) & set(GOLD_TOP30))}/30")
        if focus:
            print(f"7729 排名 算出 {focus['rank']} vs 報表 {GOLD_FOCUS['rank']}"
                  f"　市值 算出 {focus['cap']} vs 報表 {GOLD_FOCUS['cap']}")
        for i, (a, b) in enumerate(zip(got, GOLD_TOP30), 1):
            if a != b:
                print(f"  第 {i} 名不同：算出 {a}，報表 {b}")


if __name__ == "__main__":
    main()
