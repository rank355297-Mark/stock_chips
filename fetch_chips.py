"""每日大戶籌碼推薦:抓取證交所/櫃買中心/集保公開資料,計算籌碼分數,輸出上市、上櫃各前 20 名。

用法: python fetch_chips.py [YYYYMMDD]   (不給日期 = 最近一個已有盤後資料的交易日)
輸出: out/day_YYYY-MM-DD.json  (寫入網頁資料庫 days/<date> 的內容)
"""
import datetime as dt
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
OUT = os.path.join(BASE, "out")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0"}
LOOKBACK = 5            # 法人累計與均量的交易日數
MIN_AVG_LOTS = 1000     # 排除小型股:5 日均量至少 1000 張
MIN_PRICE = 10          # 排除低價股
MARKETS = ("上市", "上櫃")
TOP_N = 20              # 上市、上櫃各推薦 20 檔
TDCC_CANDIDATES = 50    # 每個市場只對前 50 名候選股查集保上週資料

# 各指標權重(合計 100)。主力分點沒有免驗證碼的公開來源,權重先設 0。
WEIGHTS = {"inst1": 25, "inst5": 25, "streak": 10, "big": 25, "margin": 15, "broker": 0}


# 櫃買中心的憑證缺少 Authority Key Identifier 欄位,Python 3.13 的嚴格模式(VERIFY_X509_STRICT)會拒絕。
# 只對櫃買中心關閉這項格式檢查,憑證鏈與主機名稱照常驗證;其他網站維持預設的嚴格檢查。
TPEX_SSL = ssl.create_default_context()
TPEX_SSL.verify_flags &= ~ssl.VERIFY_X509_STRICT


def ssl_context(url):
    return TPEX_SSL if urllib.parse.urlparse(url).hostname.endswith("tpex.org.tw") else None


def http_get(url, cache_name=None, sleep=2.5):
    if cache_name:
        p = os.path.join(CACHE, cache_name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90, context=ssl_context(url)) as r:
        raw = r.read()
    text = None
    for enc in ("utf-8-sig", "cp950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    time.sleep(sleep)
    return text


def get_json(url, cache_name):
    text = http_get(url)
    j = json.loads(text)
    # 櫃買中心在資料尚未公布時也回 stat=ok,但內容是空表,所以還要確認真的有資料列
    has_rows = bool(j.get("data")) or any(t.get("data") for t in j.get("tables") or [])
    ok = str(j.get("stat", "")).lower() == "ok" and has_rows
    if ok:  # 只快取有資料的結果,避免把「尚無資料」存起來
        with open(os.path.join(CACHE, cache_name), "w", encoding="utf-8") as f:
            f.write(text)
    return j if ok else None


CACHE_FINAL_HOUR = 22  # 證交所、櫃買中心盤後資料當天稍晚仍可能更正,資料日 22:00 以後下載的才視為定稿
DAYTRADE_FINAL_HOUR = 8  # 當沖統計更正得更晚(曾在 22:40 後仍有更正),隔天 08:00 以後下載的才視為定稿


def cache_is_final(path, cache_name):
    m = re.search(r"_(\d{8})\.json$", cache_name)
    if not m:
        return True
    day = dt.datetime.strptime(m.group(1), "%Y%m%d")
    if "daytrade" in cache_name:
        final_at = day + dt.timedelta(days=1, hours=DAYTRADE_FINAL_HOUR)
    else:
        final_at = day.replace(hour=CACHE_FINAL_HOUR)
    return dt.datetime.fromtimestamp(os.path.getmtime(path)) >= final_at


def cached_json(url, cache_name):
    p = os.path.join(CACHE, cache_name)
    if os.path.exists(p) and cache_is_final(p, cache_name):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return get_json(url, cache_name)  # 沒有快取,或快取是當天較早的暫時版本:重新下載


def num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_common_stock(code):
    return bool(re.fullmatch(r"[1-9]\d{3}", code))


# ---------- 各資料來源 ----------

def twse_inst(d):
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json",
                    f"twse_t86_{d:%Y%m%d}.json")
    if not j:
        return None
    out = {}
    for r in j["data"]:
        code = r[0].strip()
        out[code] = {"name": r[1].strip(), "foreign": num(r[4]) + num(r[7]), "trust": num(r[10]),
                     "dealer": num(r[11]), "total": num(r[18])}
    return out


def tpex_inst(d):
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={d:%Y/%m/%d}&response=json",
                    f"tpex_3i_{d:%Y%m%d}.json")
    if not j:
        return None
    out = {}
    for r in j["tables"][0]["data"]:
        code = r[0].strip()
        out[code] = {"name": r[1].strip(), "foreign": num(r[10]), "trust": num(r[13]),
                     "dealer": num(r[22]), "total": num(r[23])}
    return out


def twse_price(d):
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json",
                    f"twse_price_{d:%Y%m%d}.json")
    if not j:
        return None
    table = next((t for t in j["tables"] if t.get("fields") and t["fields"][0] == "證券代號" and "收盤價" in t["fields"]), None)
    if not table:
        return None
    out = {}
    for r in table["data"]:
        close = num(r[8])
        diff = num(r[10]) * (-1 if "-" in r[9] else 1)
        out[r[0].strip()] = {"name": r[1].strip(), "vol": num(r[2]), "value": num(r[4]), "close": close, "diff": diff,
                             "open": num(r[5]), "high": num(r[6]), "low": num(r[7])}
    return out


def tpex_price(d):
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json",
                    f"tpex_price_{d:%Y%m%d}.json")
    if not j:
        return None
    out = {}
    for r in j["tables"][0]["data"]:
        out[r[0].strip()] = {"name": r[1].strip(), "vol": num(r[7]), "value": num(r[8]), "close": num(r[2]),
                             "diff": num(r[3]), "shares": num(r[14]),
                             "open": num(r[4]), "high": num(r[5]), "low": num(r[6])}
    return out


def twse_shares():
    """上市公司已發行普通股數(證交所公司基本資料),快取 7 天。"""
    path = os.path.join(CACHE, "twse_shares.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    rows = json.loads(http_get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", sleep=1))
    out = {r["公司代號"].strip(): num(r.get("已發行普通股數或TDR原股發行股數", 0)) for r in rows if r.get("公司代號")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


# 首頁要顯示的指數:(顯示名稱, 證交所指數名稱);證交所沒有「運輸類」,以航運類指數代表
TWSE_INDICES = [("加權指數", "發行量加權股價指數"), ("航運", "航運類指數"), ("電子", "電子工業類指數"),
                ("塑化", "塑膠化工類指數"), ("紡織", "紡織纖維類指數"), ("生技", "生技醫療類指數"), ("鋼鐵", "鋼鐵類指數"), ("造紙", "造紙類指數")]


def market_indices(d):
    """當日收盤指數:加權指數、櫃買指數與幾個類股指數。回傳 [{name, close, chg, chgPct}, ...]。"""
    out = {}
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json",
                    f"twse_price_{d:%Y%m%d}.json")
    if j:
        idx = next((t for t in j["tables"] if t.get("fields") and t["fields"][0] == "指數"), {"data": []})
        rows = {r[0].strip(): r for r in idx["data"]}
        for short, full in TWSE_INDICES:
            r = rows.get(full)
            if r:
                sign = -1 if "green" in r[2] or "-" in r[2] else 1
                out[short] = {"name": short, "close": num(r[1]), "chg": round(sign * num(r[3]), 2), "chgPct": num(r[4])}
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date={d:%Y/%m/%d}&response=json",
                    f"tpex_inx_{d:%Y%m%d}.json")
    row = next((r for r in (j or {}).get("tables", [{}])[0].get("data", []) if r[0] == f"{d:%Y/%m/%d}"), None)
    if row:
        close, chg = num(row[4]), num(row[5])
        out["櫃買指數"] = {"name": "櫃買指數", "close": close, "chg": chg,
                       "chgPct": round(chg / (close - chg) * 100, 2) if close - chg else 0}
    order = ["加權指數", "櫃買指數", "航運", "電子", "塑化", "紡織", "生技", "鋼鐵", "造紙"]
    return [out[k] for k in order if k in out]


def daily_stock_rows(d):
    """當日上市、上櫃每檔普通股的行情與週轉率(成交股數 ÷ 已發行股數):{市場: [列, ...]}。"""
    result = {}
    for mk, prices, shares in (("上市", twse_price(d) or {}, twse_shares()),
                               ("上櫃", tpex_price(d) or {}, None)):
        rows = []
        for code, p in prices.items():
            sh = p.get("shares") if shares is None else shares.get(code)
            if not is_common_stock(code) or not p["vol"] or not p["close"]:
                continue
            prev = p["close"] - p["diff"]
            rows.append({"code": code, "name": p["name"], "market": mk, "close": p["close"],
                         "chgPct": round(p["diff"] / prev * 100, 2) if prev else 0,
                         "volume": round(p["vol"] / 1000), "value": round(p["value"] / 1e8, 2),
                         "turnover": round(p["vol"] / sh * 100, 2) if sh else None})
        result[mk] = rows
    return result


def daytrade_list(d):
    """證交所、櫃買中心「當日沖銷交易標的」:{代號: {"dtVol": 當沖成交股數, "noShortFirst": 暫停先賣後買}}。"""
    out = {}
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={d:%Y%m%d}&selectType=All&response=json",
                    f"twse_daytrade_{d:%Y%m%d}.json")
    for t in (j or {}).get("tables", []):
        if t.get("fields") and t["fields"][0] == "證券代號":
            for r in t["data"]:
                out[r[0].strip()] = {"dtVol": num(r[3]), "noShortFirst": r[2].strip() == "Y"}
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/intraday/stat?type=Daily&date={d:%Y/%m/%d}&response=json",
                    f"tpex_daytrade_{d:%Y%m%d}.json")
    for t in (j or {}).get("tables", []):
        if t.get("fields") and t["fields"][0] == "證券代號":
            for r in t["data"]:
                out[r[0].strip()] = {"dtVol": num(r[3]), "noShortFirst": r[2].strip() == "Y"}
    return out


DT_MIN_LOTS = 2000   # 當沖標的至少要有的當日成交量(張),太冷門的進出不易
DT_WEIGHTS = {
    # 做多「強勢續攻」:上漲、收在高點、三大法人有買、量增、週轉率與當沖熱度高
    "long": {"chg": 20, "pos": 15, "inst": 20, "surge": 15, "turnover": 20, "dt": 10},
    # 做空「隔日沖賣壓」:大漲爆量、週轉率高,但三大法人沒買(買盤來自短線資金),隔天早盤常有獲利了結賣壓
    "short": {"chg": 20, "pos": 10, "inst": 20, "surge": 20, "turnover": 20, "dt": 10},
}


def daytrade_candidates(days, n=20):
    """依交易日 days(新到舊)的最後一天資料,挑出當沖做多、做空各 n 檔(上市櫃合併排名)。"""
    d0 = days[0]
    today = daily_stock_rows(d0)
    prices = [dict(tpex_price(d) or {}, **(twse_price(d) or {})) for d in days]
    inst = dict(tpex_inst(d0) or {}, **(twse_inst(d0) or {}))
    dt_list = daytrade_list(d0)
    rows = []
    for mk, lst in today.items():
        for x in lst:
            c, p = x["code"], prices[0].get(x["code"], {})
            prev_vols = [px[c]["vol"] / 1000 for px in prices[1:] if c in px and px[c]["vol"]]
            if c not in dt_list or x["volume"] < DT_MIN_LOTS or x["close"] < MIN_PRICE or x["turnover"] is None or not prev_vols:
                continue
            hi, lo = p.get("high", 0), p.get("low", 0)
            inst_net = round(inst.get(c, {}).get("total", 0) / 1000)
            rows.append(dict(x,
                             dtRatio=round(dt_list[c]["dtVol"] / 1000 / x["volume"] * 100, 1),
                             surge=round(x["volume"] / (sum(prev_vols) / len(prev_vols)), 2),
                             pos=round((x["close"] - lo) / (hi - lo), 2) if hi > lo else 1.0,
                             instNet=inst_net, instRatio=inst_net / x["volume"],
                             shortable=not dt_list[c]["noShortFirst"]))
    ranks = {k: pct_rank([x[f] for x in rows]) for k, f in
             (("chg", "chgPct"), ("pos", "pos"), ("inst", "instRatio"), ("surge", "surge"), ("turnover", "turnover"), ("dt", "dtRatio"))}
    for i, x in enumerate(rows):
        r = {k: v[i] for k, v in ranks.items()}
        wl, ws = DT_WEIGHTS["long"], DT_WEIGHTS["short"]
        x["longScore"] = round(sum(r[k] * w for k, w in wl.items()), 1)
        # 做空:法人買超越少越好(反向),其餘與做多同向
        x["shortScore"] = round(sum((1 - r[k] if k == "inst" else r[k]) * w for k, w in ws.items()), 1)
        x["nextDaySell"] = x["chgPct"] >= 5 and x["surge"] >= 2 and x["pos"] >= 0.8 and x["instNet"] <= 0

    def tags(x, side):
        t = []
        if x.get("thesis") == "弱勢續跌": t.append("弱勢續跌")
        if x["chgPct"] >= 9.5: t.append("漲停")
        if x["chgPct"] <= -9.5: t.append("跌停")
        if x["nextDaySell"]: t.append("隔日沖賣壓")
        if x["surge"] >= 2: t.append(f"爆量 {x['surge']:.1f} 倍")
        if x["turnover"] >= 10: t.append("高週轉")
        if x["dtRatio"] >= 40: t.append("當沖熱")
        if side == "long" and x["pos"] >= 0.95: t.append("收最高")
        if side == "short" and x.get("thesis") == "弱勢續跌" and x["pos"] <= 0.05: t.append("收最低")
        if side == "long" and x["instNet"] > 0: t.append("法人買超")
        if side == "short" and x["instNet"] < 0: t.append("法人賣超")
        return t

    def reason(x, side):
        s = [f"漲跌 {x['chgPct']:+.2f}%,收盤位於當日區間 {x['pos'] * 100:.0f}%",
             f"成交量為前 4 日均量的 {x['surge']:.1f} 倍,週轉率 {x['turnover']:.2f}%,當沖比率 {x['dtRatio']:.1f}%",
             f"三大法人{'買超' if x['instNet'] >= 0 else '賣超'} {abs(x['instNet']):,} 張"]
        return ";".join(s) + "。"

    keep = ["code", "name", "market", "close", "chgPct", "volume", "value", "turnover", "dtRatio", "surge", "pos", "instNet", "shortable"]
    # 做多:今天上漲且三大法人買超;做空:今天上漲、三大法人沒有買超,且可以先賣後買(漲越多分數越高)
    long_pool = [x for x in rows if x["chgPct"] > 0 and x["instNet"] > 0]
    short_pool = [x for x in rows if x["chgPct"] > 0 and x["instNet"] <= 0 and x["shortable"]]
    # 大跌日上漲股太少時,做空以「弱勢續跌」補足:下跌、收低、法人賣超、可先賣後買(計分與做多相反)
    for i, x in enumerate(rows):
        r = {k: v[i] for k, v in ranks.items()}
        x["weakScore"] = round(sum((1 - r[k] if k in ("chg", "pos", "inst") else r[k]) * w
                                   for k, w in DT_WEIGHTS["long"].items()), 1)
    weak_pool = [x for x in rows if x["chgPct"] < 0 and x["instNet"] < 0 and x["shortable"]]
    out = {}
    picks = {"long": [(x, x["longScore"], "強勢續攻") for x in sorted(long_pool, key=lambda x: -x["longScore"])[:n]]}
    shorts = [(x, x["shortScore"], "隔日沖賣壓") for x in sorted(short_pool, key=lambda x: -x["shortScore"])[:n]]
    if len(shorts) < n:
        shorts += [(x, x["weakScore"], "弱勢續跌") for x in sorted(weak_pool, key=lambda x: -x["weakScore"])[:n - len(shorts)]]
    picks["short"] = shorts
    for side, lst in picks.items():
        out[side] = []
        for i, (x, score, thesis) in enumerate(lst, 1):
            x["thesis"] = thesis
            out[side].append(dict({k: x[k] for k in keep}, rank=i, score=score, thesis=thesis,
                                  tags=tags(x, side), reason=reason(x, side)))
    out["universe"] = len(rows)
    return out


def rank_by(rows_by_market, key, n=20):
    """依 key 由大到小排名,上市、上櫃各取前 n 名(key 為 None 的不列入)。"""
    result = {}
    for mk, rows in rows_by_market.items():
        top = sorted((x for x in rows if x[key] is not None), key=lambda x: -x[key])[:n]
        result[mk] = [dict(x, rank=i) for i, x in enumerate(top, 1)]
    return result


def margins(d):
    """回傳 (融資融券資料, 尚未公布的市場集合)。融資融券約晚上 9 點半才公布。"""
    out, missing = {}, set()
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json",
                    f"twse_margin_{d:%Y%m%d}.json")
    if j:
        for r in j["tables"][1]["data"]:
            out[r[0].strip()] = {"marginChg": num(r[6]) - num(r[5]), "marginBal": num(r[6]),
                                 "shortChg": num(r[12]) - num(r[11])}
    else:
        missing.add("上市")
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d:%Y/%m/%d}&response=json",
                    f"tpex_margin_{d:%Y%m%d}.json")
    if j:
        for r in j["tables"][0]["data"]:
            out[r[0].strip()] = {"marginChg": num(r[6]) - num(r[2]), "marginBal": num(r[6]),
                                 "shortChg": num(r[14]) - num(r[10])}
    else:
        missing.add("上櫃")
    return out, missing


def tdcc_latest():
    """集保戶股權分散表(最新一週):回傳 (資料日, {代號: 千張大戶持股%})。"""
    text = http_get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", sleep=1)
    out, date = {}, None
    for line in text.splitlines()[1:]:
        p = line.split(",")
        if len(p) < 6:
            continue
        date = p[0]
        if p[2] == "15":   # 第 15 級 = 1,000,001 股以上 = 千張大戶
            out[p[1].strip()] = float(p[5])
    with open(os.path.join(CACHE, f"tdcc_{date}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f)
    return date, out


def tdcc_for(d0):
    """取資料日期不晚於交易日 d0 的集保週資料,避免重算舊日期時用到之後才有的資料。
    集保開放資料只提供最新一週,所以舊週別要靠之前存下的 cache/tdcc_YYYYMMDD.json。"""
    date, out = tdcc_latest()
    target = f"{d0:%Y%m%d}"
    if date <= target:
        return date, out
    older = sorted(n[5:13] for n in os.listdir(CACHE)
                   if re.fullmatch(r"tdcc_\d{8}\.json", n) and n[5:13] <= target)
    if not older:
        print(f"警告:找不到 {target} 以前的集保週資料,改用最新一週 {date}")
        return date, out
    with open(os.path.join(CACHE, f"tdcc_{older[-1]}.json"), encoding="utf-8") as f:
        return older[-1], json.load(f)


class TdccHistory:
    """集保網站「股權分散表查詢」,查單一股票指定週的千張大戶比例。"""
    URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

    def __init__(self):
        cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.op.addheaders = list(UA.items())
        self.dates = None

    def _form(self):
        html = self.op.open(self.URL, timeout=60).read().decode("utf-8")
        self.dates = re.findall(r'<option value="(\d{8})"', html)
        return re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', html).group(1)

    def big_pct(self, code, date):
        cache = os.path.join(CACHE, f"tdcc_{date}_{code}.txt")
        if os.path.exists(cache):
            with open(cache) as f:
                return float(f.read())
        tok = self._form()
        data = {"SYNCHRONIZER_TOKEN": tok, "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock", "method": "submit",
                "firDate": self.dates[0], "scaDate": date, "sqlMethod": "StockNo", "stockNo": code, "stockName": ""}
        html = self.op.open(self.URL, data=urllib.parse.urlencode(data).encode(), timeout=60).read().decode("utf-8")
        time.sleep(1)
        m = re.search(r"<td[^>]*>15</td>\s*<td[^>]*>[^<]+</td>\s*<td[^>]*>[\d,]+</td>\s*<td[^>]*>[\d,]+</td>\s*<td[^>]*>([\d.]+)</td>", html)
        if not m:
            return None
        with open(cache, "w") as f:
            f.write(m.group(1))
        return float(m.group(1))


# ---------- 計算 ----------

def trading_days(end, n):
    """從 end 往回找 n 個有三大法人資料的交易日(新到舊)。"""
    days, d = [], end
    while len(days) < n and (end - d).days < 30:
        if d.weekday() < 5 and twse_inst(d):
            days.append(d)
        d -= dt.timedelta(days=1)
    return days


def pct_rank(values):
    """回傳每個值在清單中的百分位(0~1)。同分的股票取平均名次,拿到一樣的百分位。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    denom = max(1, len(values) - 1)
    pos = 0
    while pos < len(order):
        end = pos
        while end + 1 < len(order) and values[order[end + 1]] == values[order[pos]]:
            end += 1
        for k in range(pos, end + 1):
            ranks[order[k]] = (pos + end) / 2 / denom
        pos = end + 1
    return ranks


def streak(series):
    n = 0
    for v in series:  # 新到舊
        if v > 0:
            n += 1
        else:
            break
    return n


def main():
    if len(sys.argv) > 1:
        end = dt.datetime.strptime(sys.argv[1], "%Y%m%d").date()
    else:
        end = dt.date.today()
    days = trading_days(end, LOOKBACK)
    if not days:
        sys.exit("找不到最近的交易日資料")
    d0 = days[0]
    print("交易日:", [f"{d:%m/%d}" for d in days])

    inst, price = [], []
    for d in days:
        i = dict(tpex_inst(d) or {})
        mk = {c: "上櫃" for c in i}
        tw = twse_inst(d)
        i.update(tw)
        mk.update({c: "上市" for c in tw})
        inst.append((i, mk))
        p = dict(tpex_price(d) or {})
        p.update(twse_price(d) or {})
        price.append(p)
    mg, margin_missing = margins(d0)
    if margin_missing:
        print("融資融券尚未公布:", margin_missing, "→ 這一項暫不計分,晚上 9 點半後請重跑")
    tdcc_date, big = tdcc_for(d0)
    print("集保資料日:", tdcc_date)

    today_inst, market = inst[0]
    rows = []
    for code, r in today_inst.items():
        if not is_common_stock(code) or code not in price[0]:
            continue
        vols = [p[code]["vol"] / 1000 for p in price if code in p]
        avg_vol = sum(vols) / len(vols)
        close = price[0][code]["close"]
        if avg_vol < MIN_AVG_LOTS or close < MIN_PRICE:
            continue
        hist = [inst[k][0].get(code, {}).get("total", 0) / 1000 for k in range(len(days))]
        f_hist = [inst[k][0].get(code, {}).get("foreign", 0) for k in range(len(days))]
        t_hist = [inst[k][0].get(code, {}).get("trust", 0) for k in range(len(days))]
        m = mg.get(code, {})
        diff = price[0][code]["diff"]
        rows.append({
            "code": code, "name": r["name"], "market": market[code],
            "close": close, "chgPct": round(diff / (close - diff) * 100, 2) if close - diff else 0,
            "avgVol": round(avg_vol), "volume": round(price[0][code]["vol"] / 1000),
            "foreignNet": round(r["foreign"] / 1000), "trustNet": round(r["trust"] / 1000),
            "dealerNet": round(r["dealer"] / 1000), "instNet": round(r["total"] / 1000),
            "instHist": [round(x) for x in hist], "inst5d": round(sum(hist)),
            "foreignStreak": streak(f_hist), "trustStreak": streak(t_hist),
            "marginChg": round(m.get("marginChg", 0)), "marginBal": round(m.get("marginBal", 0)),
            "shortChg": round(m.get("shortChg", 0)),
            "bigPct": big.get(code), "bigChg": None, "brokerNet": None,
        })
    universe = {mk: sum(1 for x in rows if x["market"] == mk) for mk in MARKETS}
    print("符合流動性條件:", universe)

    # 第一階段:法人、連買、融資(上市、上櫃各自算百分位,分開排名)
    cands = []
    for mk in MARKETS:
        grp = [x for x in rows if x["market"] == mk]
        r1 = pct_rank([x["instNet"] / x["avgVol"] for x in grp])
        r5 = pct_rank([x["inst5d"] / (x["avgVol"] * LOOKBACK) for x in grp])
        rs = pct_rank([x["foreignStreak"] + x["trustStreak"] * 1.5 for x in grp])
        rm = pct_rank([-x["marginChg"] / x["avgVol"] for x in grp])
        # 融資融券尚未公布時,這一項不計分,其他項目依比例放大回滿分 100
        w = dict(WEIGHTS)
        if mk in margin_missing:
            scale = 100 / (100 - w["margin"])
            w = {k: (0 if k == "margin" else v * scale) for k, v in w.items()}
        for x, a, b, c, e in zip(grp, r1, r5, rs, rm):
            x["w_big"] = w["big"]
            x["parts"] = {"inst1": round(a * w["inst1"], 1), "inst5": round(b * w["inst5"], 1),
                          "streak": round(c * w["streak"], 1), "margin": round(e * w["margin"], 1),
                          "big": 0, "broker": 0}
            x["pre"] = sum(x["parts"].values())
        cands += sorted([x for x in grp if x["instNet"] > 0], key=lambda x: -x["pre"])[:TDCC_CANDIDATES]

    # 第二階段:候選股查集保上週,算千張大戶週增減
    th = TdccHistory()
    th._form()
    prev_date = next((d for d in th.dates if d < tdcc_date), None)
    for x in cands:
        prev = None
        for attempt in range(3):
            try:
                prev = th.big_pct(x["code"], prev_date)
                break
            except Exception as e:
                print("集保查詢失敗,重試", x["code"], e)
                time.sleep(3)
        if prev is not None and x["bigPct"] is not None:
            x["bigChg"] = round(x["bigPct"] - prev, 2)
    # 週增 +1 個百分點以上給滿分,減 1 個百分點以上給 0 分
    for x in cands:
        chg = x["bigChg"] if x["bigChg"] is not None else 0
        x["parts"]["big"] = round(max(0, min(1, (chg + 1) / 2)) * x.pop("w_big"), 1)
        x["score"] = round(sum(x["parts"].values()), 1)
    picks = []
    for mk in MARKETS:
        top = sorted([x for x in cands if x["market"] == mk], key=lambda x: -x["score"])[:TOP_N]
        for i, x in enumerate(top, 1):
            x["rank"] = i  # 各市場內的名次
        picks += top

    for x in picks:
        x.pop("pre", None)
        tags = []
        if x["trustStreak"] >= 3:
            tags.append(f"投信連買{x['trustStreak']}日")
        if x["foreignStreak"] >= 3:
            tags.append(f"外資連買{x['foreignStreak']}日")
        if x["bigChg"] is not None and x["bigChg"] >= 0.5:
            tags.append("大戶增持")
        if -x["marginChg"] >= x["avgVol"] * 0.05:
            tags.append("融資大減")
        if x["inst5d"] > x["avgVol"]:
            tags.append("5日買超>均量")
        x["tags"] = tags

    day_rows = daily_stock_rows(d0)   # 週轉率、成交量排行共用
    tw = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    doc = {
        "date": f"{d0:%Y-%m-%d}",
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
        "tradingDays": [f"{d:%Y-%m-%d}" for d in days],
        "tdccDate": tw(tdcc_date), "tdccPrevDate": tw(prev_date) if prev_date else None,
        "universe": len(rows), "universeByMarket": universe, "topN": TOP_N, "weights": WEIGHTS,
        "marginPending": sorted(margin_missing),
        "turnover": rank_by(day_rows, "turnover", TOP_N),
        "volumeRank": rank_by(day_rows, "volume", TOP_N),
        "indices": market_indices(d0),
        "dayTrade": daytrade_candidates(days, TOP_N),
        "filters": {"minAvgLots": MIN_AVG_LOTS, "minPrice": MIN_PRICE, "lookback": LOOKBACK},
        "picks": picks,
    }
    path = os.path.join(OUT, f"day_{doc['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("輸出:", path)
    for x in picks:
        print(x["market"], x["rank"], x["code"], x["name"], x["score"], x["instNet"], x["bigChg"], x["tags"])
    for mk, rows in doc["turnover"].items():
        print(f"週轉率 {mk} 前 3:", [(x["code"], x["name"], x["turnover"]) for x in rows[:3]])
    print("指數:", [(x["name"], x["close"], x["chgPct"]) for x in doc["indices"]])
    for side, label in (("long", "當沖做多"), ("short", "當沖做空")):
        print(f"{label} 前 5:", [(x["code"], x["name"], x["score"], x["tags"]) for x in doc["dayTrade"][side][:5]])
    for mk, rows in doc["volumeRank"].items():
        print(f"成交量 {mk} 前 3:", [(x["code"], x["name"], x["volume"]) for x in rows[:3]])


if __name__ == "__main__":
    main()
