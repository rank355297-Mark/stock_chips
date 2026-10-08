"""把證交所某一天的盤後資料存成 CSV(Excel 可直接開),並印出重點摘要。

用法: python export_twse_day.py 20261007
輸出: twse_<日期>/ 收盤行情.csv、三大法人買賣超.csv、融資融券.csv、三大法人金額.csv
"""
import csv
import datetime as dt
import os
import sys

from fetch_chips import cached_json, num

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
if not (price and inst and margin):
    sys.exit("證交所這天沒有資料(可能是休市日,或盤後資料尚未公布)")

ptab = next(t for t in price["tables"] if t.get("fields") and t["fields"][0] == "證券代號" and "收盤價" in t["fields"])
save("收盤行情.csv", ptab["fields"], [clean(r) for r in ptab["data"]])
save("三大法人買賣超.csv", inst["fields"], [[c.strip() for c in r] for r in inst["data"]])
mtab = margin["tables"][1]
mf = mtab["fields"]
save("融資融券.csv", [f"融資{x}" if 2 <= i <= 7 else f"融券{x}" if 8 <= i <= 13 else x for i, x in enumerate(mf)], mtab["data"])
if amt:
    save("三大法人金額.csv", amt["fields"], amt["data"])

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
