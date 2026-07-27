# -*- coding: utf-8 -*-
"""
分點資料回歸測試 — 對《生技籌碼與市值綜合日報》2026-07-21 那期逐筆對帳

黃金樣本是 data/broker_fixture.json（Marvin 現行 Google Apps Script 產出的真實數字）。
接上 TPEx EMdss004 之後，本站自己算出來的必須跟它一模一樣，否則就是接錯了。

⚠️ 當日窗格是硬性驗收：買超合計必須等於 69,444 股，前十大分點的名稱、淨額、
   均價都要對得上。5／10 日窗格只做參考比對 —— 現行報表沒有寫明區間怎麼取，
   差異先列出來，不當成失敗。

用法：python verify_broker.py
"""
import json, os, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")

GOLDEN_DATE = "2026-07-21"
GOLDEN_TOTAL_BUY = 69444

# 報表用的舊分點名 → 券商代號。
# 分點會改名，Marvin 的對照表是靜態的、TWSE 那兩支 API 是即時的，兩邊遲早對不上。
# 這種情況以 TWSE 的現名為準（站上顯示新名），對帳時靠代號接回來，不算失敗。
ALIAS = {
    "永豐金-苓雅": "9A9a",  # TWSE 現名「永豐金-亞灣」
    "台中銀證券": "611T",   # 造市商，TPEx 推薦證券商 API 名稱為「台中銀自營」
}


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def window(bd, end_date, n):
    """與前端 aggregate() 同一套算法，兩邊算出來必須一致"""
    dates = [d for d in bd["daily"] if d <= end_date][-n:]
    agg = {}
    for d in dates:
        for code, buy, sell, amt in bd["daily"][d]:
            a = agg.setdefault(code, [0, 0, 0.0])
            a[0] += buy
            a[1] += sell
            a[2] += amt
    total_vol = sum(a[0] for a in agg.values())
    rows = []
    for code, (buy, sell, amt) in agg.items():
        qty = buy + sell
        rows.append({
            "code": code,
            "name": bd["brokers"][code]["n"],
            "net": buy - sell,
            "avg_price": round(amt / qty, 2) if qty else 0.0,
            "pct": round(abs(buy - sell) / total_vol * 100, 2) if total_vol else 0.0,
        })
    rows.sort(key=lambda r: -r["net"])
    return rows, dates, total_vol


def main():
    bd = load("broker_daily.json")
    fx = load("broker_fixture.json")

    if GOLDEN_DATE not in bd["daily"]:
        sys.exit(f"broker_daily.json 沒有 {GOLDEN_DATE} 的資料，無法對帳")

    fails = []

    # ── 當日窗格：硬性驗收 ──────────────────────────────
    rows, dates, total_vol = window(bd, GOLDEN_DATE, 1)
    got = {r["name"]: r for r in rows}
    by_code = {r["code"]: r for r in rows}
    for old, code in ALIAS.items():
        if old not in got and code in by_code:
            got[old] = by_code[code]
    total_buy = sum(r["net"] for r in rows if r["net"] > 0)
    total_sell = sum(r["net"] for r in rows if r["net"] < 0)

    print(f"═══ 當日（{GOLDEN_DATE}）═══")
    ok = total_buy == GOLDEN_TOTAL_BUY
    print(f"買超合計 {total_buy:,}（基準 {GOLDEN_TOTAL_BUY:,}）{'✅' if ok else '❌'}")
    if not ok:
        fails.append(f"買超合計 {total_buy} != {GOLDEN_TOTAL_BUY}")
    if total_buy + total_sell != 0:
        fails.append(f"買賣超不平衡：{total_buy} + {total_sell}")
    print(f"賣超合計 {total_sell:,}　單邊總量 {total_vol:,}　分點 {len(rows)} 家\n")

    print(f"{'分點':<16}{'本站淨額':>10}{'報表':>10}  {'本站均價':>8}{'報表':>8}  "
          f"{'本站佔比':>8}{'報表':>7}")
    for side in ("buy", "sell"):
        for exp in fx["windows"]["1"][side]:
            if exp["is_other"]:
                continue
            name = exp["broker_name"]
            g = got.get(name)
            if not g:
                fails.append(f"當日缺少分點「{name}」")
                print(f"{name:<16}{'查無':>10}{exp['net_shares']:>10,}  ❌")
                continue
            bad = []
            if g["net"] != exp["net_shares"]:
                bad.append("淨額")
            if abs(g["avg_price"] - (exp["avg_price"] or 0)) > 0.005:
                bad.append("均價")
            if abs(g["pct"] - exp["share_pct"]) > 0.011:
                bad.append("佔比")
            if bad:
                fails.append(f"當日「{name}」{'／'.join(bad)}不符")
            print(f"{name:<16}{g['net']:>10,}{exp['net_shares']:>10,}  "
                  f"{g['avg_price']:>8.2f}{(exp['avg_price'] or 0):>8.2f}  "
                  f"{g['pct']:>7.2f}%{exp['share_pct']:>6.2f}%  "
                  f"{'❌ ' + '／'.join(bad) if bad else '✅'}")

    # 其他分點：報表把前十以外的加總成一列，本站也要對得上
    for side, sign in (("buy", 1), ("sell", -1)):
        exp = [r for r in fx["windows"]["1"][side] if r["is_other"]]
        if not exp:
            continue
        top = {got[r["broker_name"]]["code"] for r in fx["windows"]["1"][side]
               if not r["is_other"] and r["broker_name"] in got}
        mine = sum(r["net"] for r in rows
                   if r["code"] not in top and (r["net"] > 0) == (sign > 0))
        ok = mine == exp[0]["net_shares"]
        if not ok:
            fails.append(f"當日其他分點（{side}）{mine} != {exp[0]['net_shares']}")
        print(f"{'其他分點(' + side + ')':<16}{mine:>10,}{exp[0]['net_shares']:>10,}"
              f"  {'✅' if ok else '❌'}")

    # ── 5／10 日：參考比對 ──────────────────────────────
    for win in ("5", "10"):
        rows, dates, _ = window(bd, GOLDEN_DATE, int(win))
        if len(dates) < int(win):
            print(f"\n═══ 近 {win} 日 ═══  歷史不足（僅 {len(dates)} 天），略過")
            continue
        got = {r["name"]: r for r in rows}
        bc = {r["code"]: r for r in rows}
        for old, code in ALIAS.items():
            if old not in got and code in bc:
                got[old] = bc[code]
        print(f"\n═══ 近 {win} 日（{dates[0]} ~ {dates[-1]}）═══  參考比對，不列為失敗")
        diff = 0
        for exp in fx["windows"][win]["buy"] + fx["windows"][win]["sell"]:
            if exp["is_other"]:
                continue
            g = got.get(exp["broker_name"])
            mine = g["net"] if g else None
            if mine != exp["net_shares"]:
                diff += 1
                print(f"  {exp['broker_name']:<16}本站 {('查無' if mine is None else format(mine, ',')):>10}"
                      f"　報表 {exp['net_shares']:>10,}")
        print(f"  {'完全一致' if not diff else str(diff) + ' 檔不同（報表未載明區間取法）'}")

    print()
    if fails:
        print(f"❌ 回歸測試失敗 {len(fails)} 項：")
        for f in fails:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ 回歸測試通過：當日窗格與現行報表逐筆一致")


if __name__ == "__main__":
    main()
