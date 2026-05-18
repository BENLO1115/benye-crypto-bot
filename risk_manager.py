from strategy import Signal
from config import Config

class RiskManager:
    MIN_QTY = 0.001  # BTCUSDT 幣安最小下單量

    def __init__(self, risk_pct: float = 0.01):
        self.risk_pct = risk_pct

    def calc(self, signal: Signal, balance: float) -> dict:
        """
        依帳戶當前餘額自動計算倉位大小，確保每筆最大虧損 = 本金 1%
        balance 每次從幣安 API 即時讀取，槓桿讀 Config.LEVERAGE
        """
        risk_amt  = balance * self.risk_pct          # 1% 本金（自動跟隨帳戶餘額）
        sl_dist   = abs(signal.entry - signal.stop_loss)
        sl_pct    = sl_dist / signal.entry

        pos_value = risk_amt / sl_pct if sl_pct > 0 else 0
        qty       = max(round(pos_value / signal.entry, 3), self.MIN_QTY)
        margin    = (qty * signal.entry) / Config.LEVERAGE  # 讀設定，不寫死

        rr = abs(signal.take_profit - signal.entry) / sl_dist if sl_dist else 0

        return {
            'qty':       qty,
            'risk_amt':  risk_amt,
            'pos_value': qty * signal.entry,
            'margin':    margin,
            'rr':        round(rr, 2),
            'sl_pct':    round(sl_pct * 100, 3),
        }
