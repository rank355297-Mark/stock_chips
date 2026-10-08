"""每日大戶籌碼推薦:抓取證交所/櫃買中心/集保公開資料,計算籌碼分數,輸出上市、上櫃各前 20 名。

用法: python fetch_chips.py [YYYYMMDD]   (不給日期 = 最近一個已有盤後資料的交易日)
輸出: out/day_YYYY-MM-DD.json  (寫入網頁資料庫 days/<date> 的內容)
"""
import datetime as dt
import http.cookiejar
import json
import os
import re
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


def http_get(url, cache_name=None, sleep=2.5):
    if cache_name:
        p = os.path.join(CACHE, cache_name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
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
    ok = str(j.get("stat", "")).lower() == "ok"
    if ok:  # 只快取成功的結果,避免把「尚無資料」存起來
        with open(os.path.join(CACHE, cache_name), "w", encoding="utf-8") as f:
            f.write(text)
    return j if ok else None


def cached_json(url, cache_name):
    p = os.path.join(CACHE, cache_name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return get_json(url, cache_name)


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
    table = next(t for t in j["tables"] if t.get("fields") and t["fields"][0] == "證券代號" and "收盤價" in t["fields"])
    out = {}
    for r in table["data"]:
        close = num(r[8])
        diff = num(r[10]) * (-1 if "-" in r[9] else 1)
        out[r[0].strip()] = {"vol": num(r[2]), "value": num(r[4]), "close": close, "diff": diff}
    return out


def tpex_price(d):
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json",
                    f"tpex_price_{d:%Y%m%d}.json")
    if not j:
        return None
    out = {}
    for r in j["tables"][0]["data"]:
        out[r[0].strip()] = {"vol": num(r[7]), "value": num(r[8]), "close": num(r[2]), "diff": num(r[3])}
    return out


def margins(d):
    out = {}
    j = cached_json(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json",
                    f"twse_margin_{d:%Y%m%d}.json")
    if j:
        for r in j["tables"][1]["data"]:
            out[r[0].strip()] = {"marginChg": num(r[6]) - num(r[5]), "marginBal": num(r[6]),
                                 "shortChg": num(r[12]) - num(r[11])}
    j = cached_json(f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d:%Y/%m/%d}&response=json",
                    f"tpex_margin_{d:%Y%m%d}.json")
    if j:
        for r in j["tables"][0]["data"]:
            out[r[0].strip()] = {"marginChg": num(r[6]) - num(r[2]), "marginBal": num(r[6]),
                                 "shortChg": num(r[14]) - num(r[10])}
    return out


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
    """回傳每個值在清單中的百分位(0~1)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = pos / max(1, len(values) - 1)
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
    mg = margins(d0)
    tdcc_date, big = tdcc_latest()
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
        for x, a, b, c, e in zip(grp, r1, r5, rs, rm):
            x["parts"] = {"inst1": round(a * WEIGHTS["inst1"], 1), "inst5": round(b * WEIGHTS["inst5"], 1),
                          "streak": round(c * WEIGHTS["streak"], 1), "margin": round(e * WEIGHTS["margin"], 1),
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
        x["parts"]["big"] = round(max(0, min(1, (chg + 1) / 2)) * WEIGHTS["big"], 1)
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

    tw = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    doc = {
        "date": f"{d0:%Y-%m-%d}",
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
        "tradingDays": [f"{d:%Y-%m-%d}" for d in days],
        "tdccDate": tw(tdcc_date), "tdccPrevDate": tw(prev_date) if prev_date else None,
        "universe": len(rows), "universeByMarket": universe, "topN": TOP_N, "weights": WEIGHTS,
        "filters": {"minAvgLots": MIN_AVG_LOTS, "minPrice": MIN_PRICE, "lookback": LOOKBACK},
        "picks": picks,
    }
    path = os.path.join(OUT, f"day_{doc['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("輸出:", path)
    for x in picks:
        print(x["market"], x["rank"], x["code"], x["name"], x["score"], x["instNet"], x["bigChg"], x["tags"])


if __name__ == "__main__":
    main()
