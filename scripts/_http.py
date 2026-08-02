# -*- coding: utf-8 -*-
"""TWSE／TPEx 取資料的共用重試邏輯。

這兩個站偶爾回傳一次性的 Cloudflare 5xx，或在資料量大的端點中途斷線只傳一半
（IncompleteRead）。後者是連線層錯誤，HTTPError 專屬的重試接不住。同一份資料
重打通常就過，所以重試 3 次、間隔遞增，避免排程單純因為運氣不好就整天不更新。

2026-07-28 fetch_universe（興櫃全市場清單約 900KB）與 2026-07-31 fetch_peers
（TPEx 興櫃基本資料）先後栽在同一件事上，故集中到這裡，不要再各檔各自實作。
"""
import http.client, json, time, urllib.error, urllib.request

_RETRIABLE = (urllib.error.URLError, http.client.HTTPException, json.JSONDecodeError)


def _retry(call):
    last_err = None
    for attempt in range(3):
        try:
            return call()
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last_err = e
        except _RETRIABLE as e:
            last_err = e
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
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
