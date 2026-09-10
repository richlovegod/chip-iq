# -*- coding: utf-8 -*-
"""
發佈前的全站結構檢查 —— 便宜、不重算數字、只擋明顯壞掉的輸出

為什麼需要這一支：分點與海外夥伴各有守門員（verify_broker／verify_partners），
但 quote_daily／meta／universe／peers／broker_lut 從來沒有人在 commit 前看一眼。
已知會無聲上線的壞法：
  - fetch_tpex 某個月抓失敗只印 FAIL 然後照樣寫檔（exit 0）→ 序列帶著缺口上線，
    當月失敗時「最後更新」是今天、行情卻停在上個月
  - fetch_peers 某檔整月抓不到 → series=[]、latest=None → 前端 renderPeer 直接
    TypeError，市值戰報的排名 KPI 與整個合作夥伴頁籤都不會渲染
  - fetch_universe 7729 當天無價格 → focus=None → 前端 Top 30 表整張不畫

這裡做的是「檔案形狀對不對、日期新不新、該有的欄位在不在」。
FAIL → exit 1 → workflow 不 commit，網站停在昨天的好資料。
WARN → 印出來但照常發佈（例如黃金樣本滾出窗格，那是時間到了不是壞了）。

用法：python verify_site.py
"""
import json, os, sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")

# 「多久沒更新算壞」。台股最長連假（春節）約 9～10 天，12 天以上才是異常。
MAX_STALE_DAYS = 12

TPE = timezone(timedelta(hours=8))
TODAY = datetime.now(TPE).date()

fails, warns, oks = [], [], []


def fail(cid, msg):
    fails.append(f"[{cid}] {msg}")


def warn(cid, msg):
    warns.append(f"[{cid}] {msg}")


def ok(cid, msg):
    oks.append(f"  ✅ [{cid}] {msg}")


def failed(cid):
    return any(f.startswith(f"[{cid}]") for f in fails)


def load(name):
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        fail("S0", f"{name} 不存在")
    except json.JSONDecodeError as e:
        fail("S0", f"{name} 不是合法 JSON：{e}")
    return None


def age(iso):
    try:
        return (TODAY - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return None


def check_quote(q):
    s = q.get("series") or []
    if not s:
        fail("S1", "quote_daily.series 為空")
        return
    last = s[-1]
    for k in ("avg_price", "market_cap", "volume"):
        if not last.get(k):
            fail("S1", f"quote_daily 最後一筆（{last.get('date')}）{k} 為空或 0")
    a = age(last.get("date"))
    if a is None or a > MAX_STALE_DAYS:
        fail("S1", f"quote_daily 最後日期 {last.get('date')} 距今 {a} 天（>{MAX_STALE_DAYS}）")
    dates = [r["date"] for r in s]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        fail("S1", "quote_daily 日期未嚴格遞增或有重複")
    holes = [r["date"] for r in s if r.get("avg_price") is None or r.get("market_cap") is None]
    if holes:
        warn("S1", f"quote_daily 有 {len(holes)} 筆 avg_price／market_cap 為空（前端會略過）：{holes[:3]}")
    if not q.get("shares_outstanding"):
        fail("S1", "quote_daily.shares_outstanding 為空")
    if not failed("S1"):
        ok("S1", f"quote_daily {len(s)} 筆，最後 {last['date']} 均價 {last['avg_price']} 市值 {last['market_cap']} 億")


def check_meta(m, q):
    a = age(m.get("updated_at"))
    if a is None or a > 1:
        fail("S2", f"meta.updated_at = {m.get('updated_at')}，不是今天或昨天（台北 {TODAY}）")
    gen = m.get("generated_at") or ""
    if len(gen) < 16 or not gen.endswith("+08:00"):
        # 頁首要顯示「幾點更新的（台北）」，靠的就是這一欄；沒有它前端會退回只顯示日期。
        warn("S2", f"meta.generated_at 不是帶 +08:00 的 ISO 時間（實際：{gen or '缺'}），頁首只會顯示日期不顯示時間")
    elif gen[:10] != m.get("updated_at"):
        fail("S2", f"meta.generated_at 的日期 {gen[:10]} 與 updated_at {m.get('updated_at')} 不一致")
    t = m.get("today") or {}
    if t.get("latest_price") is None:
        warn("S2", "meta.today.latest_price 為空（總覽的「最後成交價」會顯示 —）")
    if not m.get("market_makers"):
        fail("S2", "meta.market_makers 為空")
    if not m.get("shares_outstanding"):
        fail("S2", "meta.shares_outstanding 為空")
    elif q and q.get("shares_outstanding") != m.get("shares_outstanding"):
        fail("S2", f"股數不一致：quote_daily {q.get('shares_outstanding')} vs meta {m.get('shares_outstanding')}")
    if not failed("S2"):
        stamp = (gen[:10] + " " + gen[11:16]) if len(gen) >= 16 else m.get("updated_at")
        ok("S2", f"meta 更新於 {stamp}（台北），造市商 {len(m.get('market_makers'))} 家，股數 {m.get('shares_outstanding'):,}")


def check_universe(u):
    a = age(u.get("as_of"))
    if a is None or a > MAX_STALE_DAYS:
        fail("S3", f"universe.as_of = {u.get('as_of')} 距今 {a} 天")
    top = u.get("top") or []
    if len(top) != 30:
        fail("S3", f"universe.top 應有 30 筆，實際 {len(top)}")
    f = u.get("focus")
    if not f:
        fail("S3", "universe.focus 為空（7729 當天查無價格）——市值戰報的排名 KPI 會整個不畫")
    elif not isinstance(f.get("rank"), int) or f.get("cap") is None:
        fail("S3", f"universe.focus 欄位不完整：{f}")
    for r in top:
        if r.get("cap") is None or r.get("rank") is None:
            fail("S3", f"universe.top 有筆缺 cap／rank：{r.get('code')}")
            break
    if not failed("S3"):
        ok("S3", f"universe 基準日 {u['as_of']}，7729 第 {u['focus']['rank']}／{u['total']} 名，Top 30 齊全")


def check_peers(p):
    a = age(p.get("as_of"))
    if a is None or a > MAX_STALE_DAYS:
        fail("S4", f"peers.as_of = {p.get('as_of')} 距今 {a} 天")
    peers = p.get("peers") or []
    if len(peers) < 10:
        fail("S4", f"peers 只有 {len(peers)} 檔（應為 12）")
    for x in peers:
        if not x.get("latest") or x.get("group_rank") is None:
            fail("S4", f"peers {x.get('code')} {x.get('name')} 沒有 latest／group_rank（序列為空？）——前端會 TypeError")
    checks = (p.get("validation") or {}).get("checks") or []
    if not checks:
        warn("S4", "peers.validation.checks 為空——2026-07-21 黃金樣本已滾出抓取窗格，同業比較頁的對帳表會是空的")
    if not failed("S4"):
        ok("S4", f"peers {len(peers)} 檔，基準日 {p['as_of']}，每檔都有最新市值")


def check_broker(bd, lut):
    a = age(bd.get("date_to"))
    if a is None or a > MAX_STALE_DAYS:
        fail("S5", f"broker_daily.date_to = {bd.get('date_to')} 距今 {a} 天")
    dates = bd.get("dates") or []
    if dates != sorted(dates) or len(dates) != len(bd.get("daily") or {}):
        fail("S5", "broker_daily.dates 與 daily 不一致或未排序")
    unbalanced = []
    for d in dates:
        rows = bd["daily"].get(d) or []
        if sum(r[1] for r in rows) != sum(r[2] for r in rows):
            unbalanced.append(d)
    if unbalanced:
        fail("S5", f"broker_daily 有 {len(unbalanced)} 天買進合計≠賣出合計：{unbalanced[:3]}")
    codes = {r[0] for rows in bd.get("daily", {}).values() for r in rows}
    missing = codes - set(bd.get("brokers") or {})
    if missing:
        fail("S5", f"broker_daily.brokers 缺 {len(missing)} 個代號：{sorted(missing)[:5]}")
    if lut and not (lut.get("names") and lut.get("market_makers")):
        fail("S5", "broker_lut 的 names 或 market_makers 為空")
    # 改碼對照：目標代號必須在 brokers 裡且不能自己也是舊碼（鏈式對照前端不處理）
    aliases = bd.get("aliases") or {}
    bad_alias = [o for o, n in aliases.items() if n not in (bd.get("brokers") or {}) or n in aliases]
    if bad_alias:
        fail("S5", f"broker_daily.aliases 有 {len(bad_alias)} 筆目標代號不在 brokers 或形成鏈：{bad_alias[:5]}")
    if any(v.get("unk") for c, v in (bd.get("brokers") or {}).items() if c in aliases):
        fail("S5", "有舊碼同時被標成 unk 與 alias，fetch_broker_daily 的命名邏輯壞了")
    if not failed("S5"):
        ok("S5", f"broker_daily {len(dates)} 個交易日到 {bd['date_to']}，每日買賣合計皆平衡，{len(codes)} 個代號皆有對照，{len(aliases)} 個舊碼有改碼對照")


def check_dates_agree(q, u, p, bd):
    """四份資料的最新日期應該是同一個交易日。差一天多半是手動在盤中跑的（分點檔還沒出），
    只警告；差更多就是某支抓資料腳本停在舊月份了。"""
    got = {
        "quote_daily": (q.get("series") or [{}])[-1].get("date"),
        "universe": u.get("as_of"),
        "peers": p.get("as_of"),
        "broker_daily": bd.get("date_to"),
    }
    vals = sorted(v for v in got.values() if v)
    if len(set(vals)) > 1:
        spread = (date.fromisoformat(vals[-1]) - date.fromisoformat(vals[0])).days
        (fail if spread > 4 else warn)("S6", f"四份資料的最新日期不一致（相差 {spread} 天）：{got}")
    else:
        ok("S6", f"quote／universe／peers／broker 最新日期一致：{vals[0] if vals else '—'}")


def main():
    q = load("quote_daily.json")
    m = load("meta.json")
    u = load("universe.json")
    p = load("peers.json")
    bd = load("broker_daily.json")
    lut = load("broker_lut.json")
    if q:
        check_quote(q)
    if m:
        check_meta(m, q)
    if u:
        check_universe(u)
    if p:
        check_peers(p)
    if bd:
        check_broker(bd, lut)
    if q and u and p and bd:
        check_dates_agree(q, u, p, bd)

    print(f"═══ 全站發佈前檢查（台北 {TODAY}）═══")
    for line in oks:
        print(line)
    if warns:
        print(f"\n⚠️  警告 {len(warns)} 項（照常發佈）：")
        for w in warns:
            print(f"   - {w}")
    if fails:
        print(f"\n❌ 發佈前檢查失敗 {len(fails)} 項 —— 不發佈：")
        for f in fails:
            print(f"   - {f}")
        sys.exit(1)
    print(f"\n✅ 全站檢查通過（{len(oks)} 項，{len(warns)} 項警告）")


if __name__ == "__main__":
    main()
