#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東京アクア 空きレーン スクレイパー

東京都スポーツ施設予約システム (sports.tef.or.jp) のスマホ版 /sp/ を、
ログインなし（セッションCookieのみ）で辿り、東京アクアティクスセンターの
メインプール／サブプールの「時間帯別 空き状況（○空き / ×なし / −対象外）」を取得する。

取得フロー（すべてログイン不要・実証済み）:
  1. GET /sp/                                    … セッション取得
  2. GET rsvPTransInstSrchVacantAction           … 空き検索TOP
  3. GET rsvPTransInstSrachInstnameAction        … 施設名検索ページ
  4. POST rsvPInstSrachInstnameBludAction        … 「アクア」で施設検索 → 施設一覧
  5. GET  rsvPTransInstSrchDayWeekAction         … 施設を選び期間設定画面（セッションに施設をセット）
  6. POST rsvPTransInstSrchVacantTzoneAction     … 指定日の時間帯別 空き状況HTML

結果は index.html の DATA ブロックに JSON として埋め込む。
"""

import re
import os
import sys
import ssl
import json
import time
import datetime
import unicodedata
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


class LegacyTLSAdapter(HTTPAdapter):
    """このサーバは弱いDHパラメータを使うレガシーTLS。SECLEVEL を下げて接続する。"""

    def init_poolmanager(self, *a, **kw):
        # 証明書・ホスト名の検証は有効のまま、弱いDH鍵を許すため SECLEVEL だけ下げる
        ctx = create_urllib3_context()
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)

BASE = "https://sports.tef.or.jp/sp"
UA = "Mozilla/5.0 (aqua-lanes updater; +https://github.com/)"
JST = datetime.timezone(datetime.timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

# 取得する日数（本日から）。予約システムは概ね4ヶ月弱先まで公開している（例: 7月時点で10/31まで）。
# 公開範囲いっぱいまで取得する。範囲外の日は自動的に「−受付前」表示になる。
DAYS_AHEAD = 122
# リクエスト間の待機（秒）
SLEEP = 0.3

# 施設コードのフォールバック（施設名検索が失敗した場合に使用）
FALLBACK = {
    "main": {"cd": "30000060", "flg": "3", "name": "メインプール５０ｍ"},
    "sub":  {"cd": "30000460", "flg": "3", "name": "サブプール２５ｍ"},
}

# 時間帯ラベル（全角）→ 表示用キー（時）
SLOT_KEYS = ["8", "9", "11", "13", "15", "17", "19"]


def nfkc(s):
    """全角→半角などの正規化。"""
    return unicodedata.normalize("NFKC", s or "")


def sjis_urlencode(s):
    """検索語を Shift_JIS でURLエンコード（このシステムは Shift_JIS 前提）。"""
    return urllib.parse.quote(s.encode("shift_jis"))


class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.s.mount("https://", LegacyTLSAdapter())

    def get(self, path, **kw):
        r = self.s.get(f"{BASE}/{path}", timeout=30, **kw)
        r.encoding = "shift_jis"
        time.sleep(SLEEP)
        return r.text

    def post(self, path, data, **kw):
        # data は Shift_JIS でエンコード済みの生ボディを渡す
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        r = self.s.post(f"{BASE}/{path}", data=data, headers=headers, timeout=30, **kw)
        r.encoding = "shift_jis"
        time.sleep(SLEEP)
        return r.text


def setup_session(cli):
    """セッションを確立し、施設名検索まで進める。"""
    cli.get("")  # /sp/
    cli.get("rsvPTransInstSrchVacantAction.do?displayNo=papab1000")
    cli.get("rsvPTransInstSrachInstnameAction.do?displayNo=prpaa1000&conditionMode=2")


def resolve_facilities(cli):
    """施設名「アクア」検索でメイン/サブの InstCd を解決。失敗時はフォールバック。"""
    term = sjis_urlencode("アクア")
    # login=検索 も Shift_JIS
    login = sjis_urlencode("検索")
    body = f"srchInstName={term}&displayNo=prpab3000&login={login}"
    html = cli.post("rsvPInstSrachInstnameBludAction.do", body)

    # <a href="...selectInstCd=XXXX&selectInstLendFlg=Y..."> ...名称... </a>
    found = {}
    for m in re.finditer(
        r'href="[^"]*selectInstCd=(\d+)&(?:amp;)?selectInstLendFlg=(\d+)"[^>]*>'
        r'\s*(?:<[^>]+>\s*)*([^<]+)</',
        html,
    ):
        cd, flg, name = m.group(1), m.group(2), nfkc(m.group(3)).strip()
        if "メインプール" in name:
            found["main"] = {"cd": cd, "flg": flg, "name": name}
        elif "サブプール" in name:
            found["sub"] = {"cd": cd, "flg": flg, "name": name}

    result = {}
    for key in ("main", "sub"):
        if key in found:
            result[key] = found[key]
            print(f"  resolved {key}: InstCd={found[key]['cd']} ({found[key]['name']})")
        else:
            result[key] = FALLBACK[key]
            print(f"  fallback {key}: InstCd={FALLBACK[key]['cd']}")
    return result


def select_facility(cli, fac):
    """施設を選択し、期間設定画面をセッションに読み込む（以後のTZone POSTの前提）。"""
    cli.get(
        f"rsvPTransInstSrchDayWeekAction.do?displayNo=prpad1000"
        f"&selectInstCd={fac['cd']}&selectInstLendFlg={fac['flg']}"
    )


def parse_status(sym):
    """空き記号を o(空き)/x(なし)/-(対象外) に正規化。"""
    sym = (sym or "").strip()
    if "○" in sym or "◯" in sym:
        return "o"
    if "×" in sym or "✕" in sym or "x" in sym.lower():
        return "x"
    return "-"


def fetch_day(cli, year, month, day):
    """指定日の時間帯別 空き状況を取得し、{'8':'o',...} を返す。取得不可なら None。"""
    body = (
        f"selectYear={year}&selectMonth={month:02d}&selectDay={day:02d}"
        f"&displayNo=prpae1000"
    )
    html = cli.post("rsvPTransInstSrchVacantTzoneAction.do", body)

    if "エラー画面" in html:
        return None

    # 「空き情報」リストの各 li: 「９時〜&nbsp;<span class="ff-monospace">○</span>」
    slots = {}
    # li 単位で「(全角時刻)時〜 ... 記号」を拾う
    for m in re.finditer(r"<li[^>]*>([^<]*?時〜.*?)</li>", html, re.S):
        chunk = m.group(1)
        text = nfkc(re.sub(r"<[^>]+>", "", chunk)).replace("\xa0", " ")
        hm = re.search(r"(\d{1,2})\s*時〜", text)
        if not hm:
            continue
        hour = str(int(hm.group(1)))
        sym = text.split("時〜", 1)[1]
        slots[hour] = parse_status(sym)

    if not slots:
        return None
    return slots


def day_status(slots):
    """その日のロールアップ。全時間帯(8〜19)のいずれかに○があれば open、×があれば full、無ければ na。

    注意: 8時枠だけ空く日（例: 早朝のみ空き）があるため、8時も必ず判定に含める。
    """
    vals = list(slots.values())
    if "o" in vals:
        return "open"
    if "x" in vals:
        return "full"
    return "na"


def scrape():
    cli = Client()
    setup_session(cli)
    facs = resolve_facilities(cli)

    today = datetime.datetime.now(JST).date()
    dates = [today + datetime.timedelta(days=i) for i in range(DAYS_AHEAD)]

    data = {}
    for key in ("main", "sub"):
        fac = facs[key]
        print(f"[{key}] {fac['name']} ...")
        select_facility(cli, fac)
        pool = {}
        ok = 0
        for d in dates:
            try:
                slots = fetch_day(cli, d.year, d.month, d.day)
            except Exception as e:
                print(f"    {d} error: {e}")
                slots = None
            if slots:
                pool[d.isoformat()] = {"day": day_status(slots), "slots": slots}
                ok += 1
        print(f"    {ok}/{len(dates)} days fetched")
        data[key] = pool
    return data


# ---- index.html の DATA ブロック更新（pool-calendar と同方式） ----

MARK_START = "// ===DATA-START (GitHub Actions が自動生成。手で編集しない) ==="
MARK_END = "// ===DATA-END==="


def build_block(data, updated):
    body = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    return (
        f"{MARK_START}\n"
        f'const LAST_UPDATED = "{updated}";\n'
        f"const DATA = {body};\n"
        f"{MARK_END}"
    )


def extract_existing(html):
    m = re.search(r"const DATA\s*=\s*(\{.*?\})\s*;", html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def main():
    data = scrape()

    # 施設単位で空なら既存値を温存（一時的な障害でデータを消さない）
    existing = {}
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
        existing = extract_existing(html)
    for key in ("main", "sub"):
        if not data.get(key):
            data[key] = existing.get(key, {})
            print(f"  keep existing {key} ({len(data[key])} days)")

    updated = datetime.datetime.now(JST).strftime("%Y/%-m/%-d %H:%M")
    block = build_block(data, updated)

    if not os.path.exists(INDEX):
        print(f"NOTE: {INDEX} not found — writing DATA block to data.js instead")
        with open(os.path.join(HERE, "data.js"), "w", encoding="utf-8") as f:
            f.write(block + "\n")
        return

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    new_html = re.sub(
        re.escape(MARK_START) + r".*?" + re.escape(MARK_END),
        block,
        html,
        flags=re.S,
    )
    if new_html != html:
        with open(INDEX, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"Updated {INDEX} ({updated})")
    else:
        print("No change")


if __name__ == "__main__":
    main()
