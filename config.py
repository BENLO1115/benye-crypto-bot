import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_KEY        = os.getenv('BINANCE_API_KEY')
    SECRET_KEY     = os.getenv('BINANCE_SECRET_KEY')
    DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK')
    SYMBOL         = 'BTCUSDT'   # 幣安格式（無橫線）
    LEVERAGE       = 125
    RISK_PERCENT   = 0.01   # 每筆最大虧損 1%
    SCAN_INTERVAL  = 15     # 分鐘
    DAILY_REPORT   = '09:00'
    # 模擬模式：True = 只通知不下單，False = 真實下單
    SIMULATION     = os.getenv('SIMULATION', 'true').lower() == 'true'
