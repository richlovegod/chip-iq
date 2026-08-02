# -*- coding: utf-8 -*-
"""
券商代號 → 分點名稱對照表

興櫃買賣日報表只給券商代號（如 9268），要顯示「凱基-台北」得自己併對照表。
三個來源合併，做法沿用 Marvin 2026-07-24 來信說明的方式：

  1. TWSE OpenAPI 證券商總公司基本資料  /v1/brokerService/brokerList    （64 家）
  2. TWSE OpenAPI 證券商分公司基本資料  /v1/opendata/OpenData_BRK02     （813 家）
  3. EXTRA — 上面兩支查不到的代號，取自 Marvin 自行彙整的對照表

為什麼非要第三份不可：1+2 已蓋掉日報表裡 99.2% 的代號，但「經紀部／自營」這類
總公司層級的交易代號不在其中（7729 的 44 個分點就有 2 個落在這裡）。

名稱優先序：造市商（TPEx 推薦證券商 API）> EXTRA > 分公司 > 總公司
造市商以 TPEx 為準、其餘以 Marvin 的對照表為準，產出的名稱才與現行
《生技籌碼與市值綜合日報》一致 —— 名稱對不上，對帳就沒有意義。

用法：python fetch_broker_lut.py
"""
import json, os
from datetime import date

from _http import fetch_json

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")

HQ_URL = "https://openapi.twse.com.tw/v1/brokerService/brokerList"
BRANCH_URL = "https://openapi.twse.com.tw/v1/opendata/OpenData_BRK02"
DEALER_URL = "https://www.tpex.org.tw/openapi/v1/tpex_esb_recommended_dealer"

STOCK = "7729"

# 取自 Marvin 的券商對照表（2026-07-24 來信附件，Google Sheet）。
# 只收錄 TWSE 兩支 API 查不到、或名稱與現行報表不同的代號。
# 若 fetch_broker_daily.py 印出「查無名稱」，把新代號補到這裡。
EXTRA = {
    # 7729 實際出現過、TWSE 查不到的
    "9203": "凱基-經紀",
    "9B17": "台新-台北營業部",
    # 其餘總公司層級交易代號（全市場出現過，7729 未來也可能出現）
    "538L": "第一金-華山",
    "9182": "群益金鼎經紀",
    "9699": "富邦經紀",
    "9887": "元大經紀部",
    "9A95": "永豐金經紀部",
    "910T": "群益金鼎證",
    "611T": "台中銀證券",
    "9A0T": "永豐自營",
    "538T": "第一金自營",
    # TWSE 有、但報表用另一個寫法
    "1041": "臺銀-鳳山",
    "8889": "國泰-敦南",
    "888N": "國泰-新竹",
    "8715": "陽信-台中",
    "9A81": "永豐金-匯立",
    # 台新舊代號（現行代號已換成 9B1x／9B2x）。TWSE 現行清單只留新碼，
    # 但一年份的歷史資料裡還查得到這些，不補就只剩一串代號。
    "8150": "台新", "8151": "台新-建北", "8152": "台新-新莊",
    "8156": "台新-三民", "8157": "台新-左楠", "8158": "台新-松江",
    "8159": "台新-台南", "815A": "台新-高雄", "815B": "台新-台中",
    "815H": "台新-屏東", "815S": "台新-中壢", "815Y": "台新-新營",
    # 其他 TWSE 現行清單查無、但歷史資料出現過的
    "111C": "台灣企銀-三民", "7009": "兆豐-景美", "700q": "兆豐-內湖",
}

# 分點會改名，Marvin 的對照表是靜態的、TWSE 那兩支 API 是即時的，兩邊遲早對不上。
# 這種情況以 TWSE 現名為準（站上顯示新名），對帳靠代號接回來 —— 見 verify_broker.py 的 ALIAS。
#   9A9a：報表寫「永豐金-苓雅」，TWSE 現名「永豐金-亞灣」


def _get(url):
    return fetch_json(url, {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    }, timeout=40)


def main():
    names, src = {}, {}

    for r in _get(HQ_URL):
        names[r["Code"]] = r["Name"].strip()
        src[r["Code"]] = "hq"
    n_hq = len(names)

    for r in _get(BRANCH_URL):
        code = r["證券商代號"].strip()
        names[code] = r["證券商名稱"].strip()
        src[code] = "branch"
    n_branch = len(names) - n_hq

    for code, name in EXTRA.items():
        names[code] = name
        src[code] = "extra"

    makers = []
    for r in _get(DEALER_URL):
        if r.get("SecuritiesCompanyCode") == STOCK:
            code = (r.get("DealerCode") or "").strip()
            makers.append(code)
            if r.get("DealerName"):
                names[code] = r["DealerName"].strip()
                src[code] = "dealer"

    out = {
        "updated_at": date.today().isoformat(),
        "sources": {
            "hq": f"TWSE OpenAPI 證券商總公司基本資料（{n_hq} 家）",
            "branch": f"TWSE OpenAPI 證券商分公司基本資料（{n_branch} 家）",
            "extra": f"Marvin 自行彙整之券商對照表（補 {len(EXTRA)} 個總公司層級交易代號）",
            "dealer": f"TPEx OpenAPI 興櫃推薦證券商（{STOCK} 造市商，名稱以此為準）",
        },
        "market_makers": sorted(makers),
        "names": dict(sorted(names.items())),
    }

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "broker_lut.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"對照表 {len(names)} 筆（總公司 {n_hq}／分公司 {n_branch}／補充 {len(EXTRA)}）")
    print(f"造市商：{'、'.join(f'{c} {names[c]}' for c in out['market_makers'])}")


if __name__ == "__main__":
    main()
