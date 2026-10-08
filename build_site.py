"""把 out/day_*.json 的推薦結果匯入「每天股票推薦系統」網頁。

用法: python build_site.py
輸出: ../vibe-site/index.html 與 index.html(可直接雙擊開啟的完整網頁;後者是 GitHub Pages 首頁)
      site_publish.html(發布到 Artifact 用的版本,不含 <html>/<head> 外框)
"""
import glob
import json
import os
import time
import urllib.request

from fetch_chips import CACHE, tpex_price, twse_price
import datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(BASE, "..", "vibe-site", "index.html")
SHOW_DAYS = 5

INDUSTRY = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
    "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造",
    "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學", "22": "生技醫療", "23": "油電燃氣", "24": "半導體", "25": "電腦週邊",
    "26": "光電", "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務",
    "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "91": "存託憑證",
}


def industries():
    """上市/上櫃公司基本資料的產業別,快取 7 天。"""
    path = os.path.join(CACHE, "industry.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    out = {}
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                rows = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8-sig"))
                for r in rows:
                    code = r.get("公司代號") or r.get("SecuritiesCompanyCode")
                    ind = r.get("產業別") or r.get("SecuritiesIndustryCode")
                    if code and ind:
                        out[code.strip()] = INDUSTRY.get(ind.strip(), "其他")
                break
            except Exception as e:
                print("產業別下載失敗,重試", url, e)
                time.sleep(5)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def reason(p):
    s = [f"三大法人買超 {p['instNet']:,} 張(外資 {p['foreignNet']:+,}、投信 {p['trustNet']:+,})"]
    if p["trustStreak"] >= 2:
        s.append(f"投信連買 {p['trustStreak']} 日")
    if p["foreignStreak"] >= 2:
        s.append(f"外資連買 {p['foreignStreak']} 日")
    s.append(f"近 5 日法人累計 {p['inst5d']:+,} 張")
    if p.get("bigPct") is not None:
        t = f"千張大戶持股 {p['bigPct']:.2f}%"
        if p.get("bigChg") is not None:
            t += f",較前一週{'增加' if p['bigChg'] >= 0 else '減少'} {abs(p['bigChg']):.2f} 個百分點"
        s.append(t)
    if p["marginChg"]:
        s.append(f"融資{'減少' if p['marginChg'] < 0 else '增加'} {abs(p['marginChg']):,} 張")
    return ";".join(s) + "。"


def main():
    ind = industries()
    docs = []
    for path in sorted(glob.glob(os.path.join(BASE, "out", "day_*.json")), reverse=True)[:SHOW_DAYS]:
        with open(path, encoding="utf-8") as f:
            docs.append(json.load(f))
    days = []
    for doc in docs:
        tdays = [dt.date.fromisoformat(x) for x in reversed(doc["tradingDays"])]  # 舊 → 新
        prices = []
        for d in tdays:
            p = dict(tpex_price(d) or {})
            p.update(twse_price(d) or {})
            prices.append(p)
        picks = []
        for p in doc["picks"]:
            picks.append({
                "rank": p["rank"], "code": p["code"], "name": p["name"], "market": p["market"],
                "sector": ind.get(p["code"], "其他"), "close": p["close"], "chg": p["chgPct"],
                "vol": p["volume"], "score": round(p["score"]),
                "series": [px[p["code"]]["close"] if p["code"] in px else None for px in prices],
                "tags": p["tags"] or ["法人買超"], "reason": reason(p),
            })
        days.append({"date": doc["date"], "seriesDates": [f"{d.month}/{d.day}" for d in tdays],
                     "tdccDate": doc["tdccDate"], "universe": doc["universe"],
                     "universeByMarket": doc.get("universeByMarket", {}), "picks": picks})
    data = {"generatedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "days": days}

    with open(os.path.join(BASE, "site_template.html"), encoding="utf-8") as f:
        body = f.read().replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
    with open(os.path.join(BASE, "site_publish.html"), "w", encoding="utf-8") as f:
        f.write(body)
    page = ('<!doctype html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "<style>body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>\n"
            f"</head>\n<body>\n{body}\n</body>\n</html>\n")
    for target in (SITE, os.path.join(BASE, "index.html")):  # vibe-site 與本資料夾(GitHub Pages 首頁)
        with open(target, "w", encoding="utf-8") as f:
            f.write(page)
        print("已匯入", [d["date"] for d in days], "→", os.path.abspath(target))


if __name__ == "__main__":
    main()
