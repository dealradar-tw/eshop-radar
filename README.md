# 台區特價雷達

台灣區 eShop 特價與價格追蹤（非官方個人工具）。

- 網站：`docs/`（GitHub Pages）
- 每日更新：`.github/workflows/update.yml` 會在台灣時間每天 02:10、14:10 自動執行 `scripts/update.py`
- 每週一完整重掃一次全部編號
- 價格歷史：`data/history.json`
- 降價通知（選用）：在 `data/wishlist.txt` 填收藏編號，並設定 `TELEGRAM_TOKEN`、`TELEGRAM_CHAT_ID` 兩個 Secrets
