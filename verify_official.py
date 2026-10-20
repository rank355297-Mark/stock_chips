"""重新下載證交所、櫃買中心的官方資料,核對 out/day_YYYY-MM-DD.json 是否一致(不使用 cache/)。

用法: python verify_official.py 20261020 [20261019 ...]
核對項目:推薦股的收盤價、三大法人、外資、投信、成交量、融資增減與餘額;
          上市、上櫃成交量前 20 名;加權指數與櫃買指數;當沖名單的當沖比率與可否先賣後買。
全部一致時結束代碼為 0,有任何不一致為 1。
"""
import json
import os
import re
import sys

from fetch_chips import http_get

BASE = os.path.dirname(os.path.abspath(__file__))


def get(url):
    return json.loads(http_get(url))  # 不帶快取名稱:一律重新下載


def n(s):
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def common(code):
    return bool(re.fullmatch(r"[1-9]\d{3}", code))


def verify(d):
    ymd, slash = f"{d[:4]}-{d[4:6]}-{d[6:]}", f"{d[:4]}/{d[4:6]}/{d[6:]}"
    doc = json.load(open(os.path.join(BASE, "out", f"day_{ymd}.json"), encoding="utf-8"))
    bad = 0

    t86 = {r[0].strip(): r for r in get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json")["data"]}
    pj = get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json")
    twp = {r[0].strip(): r for r in next(t for t in pj["tables"] if t.get("fields") and t["fields"][0] == "證券代號")["data"]}
    idx = {r[0].strip(): r for r in pj["tables"][0]["data"]}
    twm = {r[0].strip(): r for r in get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json")["tables"][1]["data"]}
    o3 = {r[0].strip(): r for r in get(f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json")["tables"][0]["data"]}
    op = {r[0].strip(): r for r in get(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={slash}&type=EW&response=json")["tables"][0]["data"]}
    om = {r[0].strip(): r for r in get(f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={slash}&response=json")["tables"][0]["data"]}
    inx = next(r for r in get(f"https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date={slash}&response=json")["tables"][0]["data"] if r[0] == slash)
    dt = {}
    for u in (f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={d}&selectType=All&response=json",
              f"https://www.tpex.org.tw/www/zh-tw/intraday/stat?type=Daily&date={slash}&response=json"):
        for r in get(u)["tables"][1]["data"]:
            dt[r[0].strip()] = (n(r[3]), r[2].strip() != "Y")

    # 推薦股
    nb = 0
    for p in doc["picks"]:
        c = p["code"]
        if p["market"] == "上市":
            got = (n(twp[c][8]), round(n(t86[c][18]) / 1000), round((n(t86[c][4]) + n(t86[c][7])) / 1000),
                   round(n(t86[c][10]) / 1000), round(n(twp[c][2]) / 1000),
                   round(n(twm[c][6]) - n(twm[c][5])), round(n(twm[c][6])))
        else:
            got = (n(op[c][2]), round(n(o3[c][23]) / 1000), round(n(o3[c][10]) / 1000), round(n(o3[c][13]) / 1000),
                   round(n(op[c][7]) / 1000), round(n(om[c][6]) - n(om[c][2])), round(n(om[c][6])))
        mine = (p["close"], p["instNet"], p["foreignNet"], p["trustNet"], p["volume"], p["marginChg"], p["marginBal"])
        if mine != got:
            nb += 1
            print("  推薦股不一致", c, p["name"], mine, got)
    print(f"推薦股 {len(doc['picks'])} 檔(收盤、法人、外資、投信、成交量、融資),不一致 {nb} 檔")
    bad += nb

    # 成交量排行
    for mk, src, ci, vi in (("上市", twp, 8, 2), ("上櫃", op, 2, 7)):
        top = sorted(((c, round(n(r[vi]) / 1000)) for c, r in src.items() if common(c) and n(r[ci]) > 0), key=lambda x: -x[1])[:20]
        ok = [(x["code"], x["volume"]) for x in doc["volumeRank"][mk]] == top
        print(f"成交量排行 {mk} 前 20 名一致: {ok}")
        bad += not ok

    # 指數
    ind = {x["name"]: x for x in doc["indices"]}
    ok = (abs(ind["加權指數"]["close"] - n(idx["發行量加權股價指數"][1])) < 1e-6
          and abs(ind["櫃買指數"]["close"] - n(inx[4])) < 1e-6 and abs(ind["櫃買指數"]["chg"] - n(inx[5])) < 1e-6)
    print(f"加權指數、櫃買指數一致: {ok} {ind['加權指數']['close']} {ind['櫃買指數']['close']}")
    bad += not ok

    # 當沖名單
    nb, total = 0, 0
    for side in ("long", "short"):
        for x in doc["dayTrade"][side]:
            total += 1
            vol, shortable = dt[x["code"]]
            if round(vol / 1000 / x["volume"] * 100, 1) != x["dtRatio"] or shortable != x["shortable"]:
                nb += 1
                print("  當沖不一致", side, x["code"], x["name"])
    short_ok = all(x["shortable"] for x in doc["dayTrade"]["short"])
    print(f"當沖比率與先賣後買 {total} 檔,不一致 {nb} 檔;做空全部可先賣後買: {short_ok}")
    bad += nb + (not short_ok)
    return bad


if __name__ == "__main__":
    total_bad = 0
    for d in sys.argv[1:]:
        print(f"== {d}")
        total_bad += verify(d)
    print("全部一致" if total_bad == 0 else f"共 {total_bad} 項不一致")
    sys.exit(1 if total_bad else 0)
