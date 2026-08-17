# -*- coding: utf-8 -*-
"""TWSE／TPEx 取資料的共用重試邏輯。

這兩個站偶爾回傳一次性的 Cloudflare 5xx，或在資料量大的端點中途斷線只傳一半
（IncompleteRead）。後者是連線層錯誤，HTTPError 專屬的重試接不住。同一份資料
重打通常就過，所以會重試數次、間隔遞增，避免排程單純因為運氣不好就整天不更新。

2026-07-28 fetch_universe（興櫃全市場清單約 900KB）與 2026-07-31 fetch_peers
（TPEx 興櫃基本資料）先後栽在同一件事上，故集中到這裡，不要再各檔各自實作。

2026-08-17 再放寬一次：原本 3 次、總共只等 6 秒，接不住 TPEx 連續數十秒回
Cloudflare 錯誤頁（解 JSON 時就是 JSONDecodeError）的情況——8/14 與 8/17 各掛
一次。改成 5 次、總共等 76 秒。

上限刻意留在一分多鐘：重試耗盡就往外拋、整個腳本結束，所以真正全站掛掉時
只會多花這一輪的時間，不會每個 URL 各燒一輪而撐爆 workflow 的 30 分鐘上限。
"""
import http.client, json, time, urllib.error, urllib.request

_RETRIABLE = (urllib.error.URLError, http.client.HTTPException, json.JSONDecodeError)

_BACKOFF = (3, 8, 20, 45)  # 每次失敗後等待的秒數；嘗試次數 = len + 1


def _retry(call):
    last_err = None
    for attempt in range(len(_BACKOFF) + 1):
        try:
            return call()
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last_err = e
        except _RETRIABLE as e:
            last_err = e
        if attempt < len(_BACKOFF):
            time.sleep(_BACKOFF[attempt])
    raise last_err


def fetch_json(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    return _retry(lambda: json.load(urllib.request.urlopen(req, timeout=timeout)))


def fetch_bytes(url, headers, timeout=60):
    """4xx 直接往外拋，呼叫端要自己處理（例如非交易日的 302／404）。"""
    req = urllib.request.Request(url, headers=headers)

    def call():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    return _retry(call)
