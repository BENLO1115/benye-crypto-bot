# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概述

這是一個部署在 Railway 上的 Binance Futures 自動交易機器人，結合 Smart Money Concepts（SMC）技術分析策略，每 15 分鐘掃描市場並自動下單、管理風險，並透過 Discord Webhook 推送通知。

---

## 執行指令

```bash
# 啟動交易機器人 (Worker)
python main.py

# 啟動 Web 儀表板 (Flask, port 8080)
python dashboard_server.py

# 執行回測（預設 180 天）
python backtest.py --days 180
```

**無測試套件、無 linting 工具**。開發驗證主要靠回測（`backtest.py`）和 `SIMULATION=true` 模式進行手動測試。

---

## 環境變數設定

在根目錄建立 `.env` 檔案：

```
BINANCE_API_KEY=
BINANCE_SECRET_KEY=
DISCORD_WEBHOOK=
SIMULATION=false          # true = 紙上交易，不真實下單
PAUSE_TRADING=false       # true = 手動暫停所有交易（無需重新部署）
BINANCE_API_KEY_DATE=     # API 建立日期，用於 90 天到期提醒
BOT_START_DATE=           # 機器人啟動日期，過濾舊的帳戶歷史記錄
```

`config.py` 內的硬編碼參數（不從環境變數讀取）：
- `SYMBOL = 'BTCUSDT'`、`LEVERAGE = 125`、`RISK_PERCENT = 0.01`（1% 單筆風險）
- `SCAN_INTERVAL = 15`（分鐘）、`MIN_RR = 1.5`、`MAX_DAILY_LOSS_PCT = 0.03`
- `LIMIT_EXPIRY_MIN = 60`（限價單若未成交則 60 分鐘後取消）

---

## 架構概覽

### 兩個獨立進程（Procfile 定義）

| 進程 | 檔案 | 職責 |
|------|------|------|
| `worker` | `main.py` | 交易主循環（排程掃描、下單、狀態管理） |
| `web` | `dashboard_server.py` | Flask 儀表板 REST API + HTML 前端 |

這兩個進程**完全獨立**，不共享記憶體。`dashboard_server.py` 直接向 Binance API 拉取資料，不依賴 `main.py` 的狀態。

### 核心模組職責

```
main.py          → 排程器 + 狀態機 + 進場/出場邏輯
strategy.py      → SMC 訊號生成（多時間框架分析）
binance_client.py → Binance Futures REST API 封裝（帶重試）
risk_manager.py  → 倉位大小計算（1% 風險公式）
discord_notifier.py → Discord Webhook 通知
analyst.py       → 歷史績效分析（每日 Discord 報告）
config.py        → 全域常數與環境變數讀取
```

### 交易流程（`main.py` 的 `scan()`）

1. 檢查 `PAUSE_TRADING`、每日虧損上限（3%）、已有未平倉部位
2. 呼叫 `strategy.py` 取得 `Signal`（方向、進場、TP、SL、理由）
3. 透過 `risk_manager.py` 計算倉位大小
4. 呼叫 `binance_client.py` 下限價單
5. 寫入 `pending.json` 追蹤掛單狀態
6. 後續掃描中確認成交後寫入 `position.json`，並補設 TP/SL

### SMC 策略邏輯（`strategy.py`）

多時間框架由大到小分析：
- **Daily**：確定趨勢方向（看多或看空偏向）
- **4H / 1H**：找出 Order Block（OB）和結構突破（BOS）
- **15M**：尋找進場觸發（Fair Value Gap、流動性掃蕩確認）
- 必須同時滿足：趨勢偏向 + 掃過流動性 + OB/FVG 支撐 + BB 支撐 + 最低 RR 1.5

### 狀態持久化（JSON 檔案）

| 檔案 | 內容 | 生命週期 |
|------|------|---------|
| `state.json` | 每日餘額基準、當日交易次數 | 每日重置 |
| `pending.json` | 待成交限價單資訊 | 成交或取消後刪除 |
| `position.json` | 當前持倉（進場、TP、SL） | 平倉後刪除 |
| `trades_history.json` | Analyst 模組的歷史交易記錄 | 永久保留 |
| `bot.log` | 輪替日誌（2MB × 3 備份） | 自動輪替 |

---

## 部署（Railway）

`railway.toml` 設定：nixpacks 自動建置（依據 `runtime.txt` 選 Python 3.11 + `requirements.txt` 安裝套件），失敗自動重啟（最多 3 次）。部署後兩個進程同時運行。
