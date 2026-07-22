# -*- coding: utf-8 -*-
"""
7729 籌碼面板 — TPEx 資料抓取

資料源（皆為 TPEx 公開資料，實測可程式化取得）：
  1. 興櫃個股歷史行情  /www/zh-tw/emerging/historical?date=YYYY/MM&code=XXXX&response=json
     → 每日 成交股數/金額/最高/最低/均價/筆數（**無最後成交價**）
  2. 興櫃當日行情表    /openapi/v1/tpex_esb_latest_statistics
     → 含 LatestPrice（最後成交價，僅當日，不進歷史）
  3. 興櫃資本額排名    /openapi/v1/tpex_esb_capitals_rank
  4. 興櫃推薦證券商    /openapi/v1/tpex_esb_recommended_dealer
     → 造市商名單。造市商均價顯示 0 屬正常，UI 需據此標記。

用法：python fetch_tpex.py [YYYY/MM ...]   (預設抓最近 6 個月)
"""
import json, os, sys, time, urllib.request
from datetime import date

STOCK = "7729"
STOCK_NAME = "仲恩生醫"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")

# 已發行股數改為每次執行從 MOPS「已發行普通股數」抓最新值（見 fetch_shares）。
# 以下僅為 API 不可用時的後備值，來自 2026-07-23 抓到的官方數字。
#
# ⚠️ 兩個看似合理但都會算錯的來源，不要用：
#    1. TPEx 興櫃資本額排名的 848.36 百萬元 → 推得 84.836M 股（該表為舊值，差 5.3%）
#    2. 實收資本額 ÷ 10 → 部分公司面額不是 10 元（如沛爾 6949、康霈 6919 為 0.5 元）
SHARES_FALLBACK = 80_572_991

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://www.tpex.org.tw/",
}


def _get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    return json.load(urllib.request.urlopen(req, timeout=40))


def roc_to_iso(s):
    """115/07/21 -> 2026-07-21"""
    y, m, d = s.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def num(s):
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_month(ym):
    """ym 例 '2026/07'。⚠️ 端點的 date 必須是完整日期 YYYY/MM/DD，
    只給年月會被忽略並一律回傳當月，切勿改回年月格式。"""
    url = (f"https://www.tpex.org.tw/www/zh-tw/emerging/historical"
           f"?date={ym}/01&code={STOCK}&response=json")
    j = _get(url)
    if j.get("stat") != "ok":
        return []
    out = []
    for r in j["tables"][0]["data"]:
        volume = num(r[1])
        if not volume:            # 無成交日跳過
            continue
        out.append({
            "date": roc_to_iso(r[0]),
            "volume": int(volume),           # 成交股數
            "turnover": int(num(r[2]) or 0),  # 成交金額(元)
            "high": num(r[3]),
            "low": num(r[4]),
            "avg_price": num(r[5]),          # 成交均價 = 金額/股數
            "trades": int(num(r[6]) or 0),
        })
    return out


def fetch_today():
    """當日行情：唯一有 LatestPrice(最後成交價) 的來源"""
    for r in _get("https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"):
        if r.get("SecuritiesCompanyCode") == STOCK:
            return {
                "date": roc_to_iso("/".join([r["Date"][:3], r["Date"][3:5], r["Date"][5:7]])),
                "latest_price": num(r.get("LatestPrice")),
                "avg_price": num(r.get("Average")),
                "prev_avg_price": num(r.get("PreviousAveragePrice")),
                "high": num(r.get("Highest")),
                "low": num(r.get("Lowest")),
                "volume": int(num(r.get("TransactionVolume")) or 0),
            }
    return None


def fetch_market_makers():
    out = []
    for r in _get("https://www.tpex.org.tw/openapi/v1/tpex_esb_recommended_dealer"):
        if r.get("SecuritiesCompanyCode") == STOCK:
            out.append({"dealer_code": r.get("DealerCode"), "dealer_name": r.get("DealerName")})
    return out


def fetch_shares():
    """MOPS 公開資訊觀測站 興櫃公司基本資料 → 已發行普通股數（市值計算的分母）"""
    try:
        for r in _get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_R"):
            if r.get("SecuritiesCompanyCode") == STOCK:
                n = num(r.get("IssueShares"))
                if n:
                    return int(n), "MOPS 已發行普通股數"
    except Exception as e:
        print(f"  股數抓取失敗（{type(e).__name__}），改用後備值")
    return SHARES_FALLBACK, "後備值（MOPS 抓取失敗）"


def fetch_capital():
    for r in _get("https://www.tpex.org.tw/openapi/v1/tpex_esb_capitals_rank"):
        if r.get("SecuritiesCompanyCode") == STOCK:
            return {"capital_musd": num(r.get("Capital")), "rank": int(num(r.get("Rank")) or 0)}
    return None


def main():
    months = sys.argv[1:]
    if not months:
        today = date.today()
        y, m = today.year, today.month
        months = []
        for _ in range(24):
            months.append(f"{y:04d}/{m:02d}")
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        months.reverse()

    quotes = {}
    for ym in months:
        try:
            for row in fetch_month(ym):
                quotes[row["date"]] = row
            print(f"  {ym}: ok")
        except Exception as e:
            print(f"  {ym}: FAIL {type(e).__name__}: {e}")
        time.sleep(0.6)

    series = [quotes[d] for d in sorted(quotes)]

    shares, shares_src = fetch_shares()

    # 市值 = 均價 × 已發行股數（與 fetch_peers.py 同一套算法，兩邊數字必須一致）
    for row in series:
        row["market_cap"] = round(row["avg_price"] * shares / 1e8, 2) if row["avg_price"] else None

    today_q = fetch_today()
    makers = fetch_market_makers()
    capital = fetch_capital()

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "quote_daily.json"), "w", encoding="utf-8") as f:
        json.dump({"stock_id": STOCK, "stock_name": STOCK_NAME,
                   "shares_outstanding": shares,
                   "series": series}, f, ensure_ascii=False, indent=1)

    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "stock_id": STOCK, "stock_name": STOCK_NAME, "market": "興櫃",
            "updated_at": date.today().isoformat(),
            "today": today_q,
            "capital": capital,
            "market_makers": makers,
            "shares_outstanding": shares,
            "shares_source": shares_src,
            "shares_note": "MOPS 已發行普通股數，每次執行重抓；與 fetch_peers.py 同源，確保全站市值一致",
            "sources": {
                "quote_history": "TPEx 興櫃個股歷史行情（無最後成交價，僅最高/最低/均價）",
                "today": "TPEx OpenAPI 興櫃股票當日行情表",
                "market_makers": "TPEx OpenAPI 興櫃推薦證券商",
                "broker_daily": "⚠️ 尚未接入 — 官方無興櫃分點資料，待公司現行系統提供",
            },
            "data_status": {"quote": "live", "marketcap": "live",
                            "market_makers": "live", "broker": "fixture"},
        }, f, ensure_ascii=False, indent=1)

    print(f"\n交易日 {len(series)} 天：{series[0]['date']} ~ {series[-1]['date']}")
    print(f"造市商：{'、'.join(m['dealer_name'] for m in makers)}")
    if today_q:
        print(f"最新：{today_q['date']} 均價 {today_q['avg_price']} 最後成交 {today_q['latest_price']}")


if __name__ == "__main__":
    main()
