# stock_chips:台股大戶籌碼推薦

抓取證交所、櫃買中心與集保結算所的盤後公開資料,計算籌碼分數,每天推薦上市 20 檔、上櫃 20 檔。

## 檔案

| 檔案 | 用途 |
|---|---|
| `fetch_chips.py` | 抓取三大法人、收盤行情、融資融券、集保大戶資料並計算分數,輸出 `out/day_YYYY-MM-DD.json` |
| `build_site.py` | 把 `out/` 的推薦結果嵌入 `site_template.html`,產生 `../vibe-site/index.html` |
| `export_twse_day.py` | 把證交所某一天的盤後資料存成 CSV,並印出大盤與法人摘要 |
| `index.html` | `build_site.py` 產生的推薦網頁(GitHub Pages 首頁) |
| `site_template.html` | 推薦網頁的模板(`site_template_v1.html` 為舊版) |
| `chips_radar.html` | 「籌碼雷達」網頁 |
| `out/` | 每日推薦結果 |
| `twse_20261007/` | 2026/10/07 證交所盤後資料 CSV |

## 使用方式

需要 Python 3,只用標準函式庫。

```bash
python fetch_chips.py            # 最近一個交易日
python fetch_chips.py 20261007   # 指定日期
python build_site.py             # 產生網頁
python export_twse_day.py 20261007
```

融資融券資料約晚上 9 點半後公布,建議晚上或隔天早上執行。下載的原始資料會快取在 `cache/`(不納入版本控制)。

## 評分方式

法人當日買超 25 + 法人 5 日累計 25 + 外資/投信連買 10 + 千張大戶週增減 25 + 融資減少 15,上市、上櫃分開計分排名。只計 5 日均量 1,000 張以上、股價 10 元以上的普通股。

僅供研究參考,不構成投資建議。
