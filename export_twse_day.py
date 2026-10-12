"""把證交所某一天的盤後資料存成 CSV(Excel 可直接開),並印出重點摘要。

用法: python export_twse_day.py 20261007
輸出: twse_<日期>/ 收盤行情.csv、三大法人買賣超.csv、融資融券.csv、三大法人金額.csv、
      成交量前20名.csv、週轉率前20名.csv(上市、上櫃各前 20 名普通股)、當沖做多20.csv、當沖做空20.csv
"""
import csv
import datetime as dt
import os
import sys

from fetch_chips import TOP_N, cached_json, daily_stock_rows, daytrade_candidates, num, rank_by, trading_days

d = dt.datetime.strptime(sys.argv[1], "%Y%m%d").date()
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"twse_{d:%Y%m%d}")
os.makedirs(base, exist_ok=True)


def save(name, fields, rows):
    with open(os.path.join(base, name), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)


def clean(r):
    return [str(x).replace("<p style= color:red>+</p>", "+").replace("<p style= color:green>-</p>", "-")
            .replace("<p> </p>", "").replace("<p>X</p>", "X").strip() for x in r]


price = cached_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json",
                    f"twse_price_{d:%Y%m%d}.json")
inst = cached_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json",
                   f"twse_t86_{d:%Y%m%d}.json")
margin = cached_json(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json",
                     f"twse_margin_{d:%Y%m%d}.json")
amt = cached_json(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={d:%Y%m%d}&response=json",
                  f"twse_bfi82u_{d:%Y%m%d}.json")
if not (price and inst):
    sys.exit("證交所這天沒有資料(可能是休市日,或盤後資料尚未公布)")

ptab = next(t for t in price["tables"] if t.get("fields") and t["fields"][0] == "證券代號" and "收盤價" in t["fields"])
save("收盤行情.csv", ptab["fields"], [clean(r) for r in ptab["data"]])
save("三大法人買賣超.csv", inst["fields"], [[c.strip() for c in r] for r in inst["data"]])
if margin:
    mtab = margin["tables"][1]
    mf = mtab["fields"]
    save("融資融券.csv", [f"融資{x}" if 2 <= i <= 7 else f"融券{x}" if 8 <= i <= 13 else x for i, x in enumerate(mf)], mtab["data"])
else:
    print("融資融券尚未公布(約晚上 9 點半),本次未輸出 融資融券.csv,公布後請重跑")
if amt:
    save("三大法人金額.csv", amt["fields"], amt["data"])

# 上市、上櫃普通股的成交量與週轉率排行(各前 20 名)
day_rows = daily_stock_rows(d)
for fname, key in (("成交量前20名.csv", "volume"), ("週轉率前20名.csv", "turnover")):
    ranked = rank_by(day_rows, key, TOP_N)
    save(fname, ["市場", "名次", "代號", "名稱", "收盤", "漲跌幅(%)", "成交量(張)", "成交金額(億)", "週轉率(%)"],
         [[x["market"], x["rank"], x["code"], x["name"], x["close"], x["chgPct"], x["volume"], x["value"],
           "" if x["turnover"] is None else x["turnover"]] for mk in ("上市", "上櫃") for x in ranked[mk]])

# 當沖熱門標的:做多、做空各 20 檔(上市櫃合併)
dt_picks = daytrade_candidates(trading_days(d, 5), TOP_N)
for fname, side in (("當沖做多20.csv", "long"), ("當沖做空20.csv", "short")):
    save(fname, ["名次", "市場", "代號", "名稱", "分數", "收盤", "漲跌幅(%)", "成交量(張)", "週轉率(%)", "當沖比率(%)",
                 "量增倍數", "收盤位置(%)", "三大法人(張)", "可先賣後買", "標籤", "理由"],
         [[x["rank"], x["market"], x["code"], x["name"], x["score"], x["close"], x["chgPct"], x["volume"], x["turnover"],
           x["dtRatio"], x["surge"], round(x["pos"] * 100), x["instNet"], "是" if x["shortable"] else "否",
           "、".join(x["tags"]), x["reason"]] for x in dt_picks[side]])

# ---- 摘要 ----
idx = next(t for t in price["tables"] if t.get("fields") and t["fields"][0] == "指數")
taiex = next(r for r in idx["data"] if r[0] == "發行量加權股價指數")
sign = "-" if "green" in taiex[2] else "+"
stat = next(t for t in price["tables"] if t.get("fields") and t["fields"][0] == "成交統計")
print(f"=== 證交所 {d:%Y/%m/%d} ===")
print(f"加權指數 {taiex[1]}  {sign}{taiex[3]} ({sign}{taiex[4].lstrip('-')}%)")
print(f"大盤成交金額 {num(stat['data'][-1][1]) / 1e8:,.0f} 億元")
if amt:
    for r in amt["data"]:
        print(f"  {r[0]}: 買賣差額 {num(r[3]) / 1e8:+,.1f} 億元")

stocks = {r[0].strip(): r for r in ptab["data"]}
common = [r for r in inst["data"] if r[0].strip() in stocks and len(r[0].strip()) == 4 and not r[0].startswith("0")]


def top(rows, col, n=10, rev=True):
    return sorted(rows, key=lambda r: num(r[col]), reverse=rev)[:n]


for title, col, rev in [("外資買超", 4, True), ("外資賣超", 4, False), ("投信買超", 10, True)]:
    print(f"\n{title} 前 10 名(張)")
    for r in top(common, col, rev=rev):
        print(f"  {r[0].strip()} {r[1].strip():<6} {num(r[col]) / 1000:+,.0f}")

rows = [r for r in ptab["data"] if len(r[0].strip()) == 4 and not r[0].startswith("0") and num(r[8]) > 0]
print("\n成交金額 前 10 名(億元)")
for r in top(rows, 4):
    print(f"  {r[0].strip()} {r[1].strip():<6} {num(r[4]) / 1e8:,.1f}  收 {r[8]}")
print("\n已輸出到", base)
