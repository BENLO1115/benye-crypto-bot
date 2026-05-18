import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_KEY        = os.getenv('BINANCE_API_KEY')
    SECRET_KEY     = os.getenv('BINANCE_SECRET_KEY')
    DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK')
    SYMBOL         = 'BTCUSDT'
    LEVERAGE       = 125
    RISK_PERCENT   = 0.01        # 每筆最大虧損 1%
    SCAN_INTERVAL  = 15          # 分鐘
    DAILY_REPORT   = '09:00'
    SIMULATION     = os.getenv('SIMULATION', 'true').lower() == 'true'

    MIN_RR              = 1.5    # 低於此 RR 不進場
    MAX_DAILY_LOSS_PCT  = 0.03   # 每日最大虧損 3%，觸發後停止當日交易
    LIMIT_EXPIRY_MIN    = 60     # Limit 單超過幾分鐘未成交自動取消

    # 手動暫停開關（Railway 環境變數設 PAUSE_TRADING=true 即可）
    PAUSE_TRADING  = os.getenv('PAUSE_TRADING', 'false').lower() == 'true'

    # API 金鑰建立日期（格式 YYYY-MM-DD），用於 90 天到期提醒
    API_KEY_DATE   = os.getenv('BINANCE_API_KEY_DATE', '')
