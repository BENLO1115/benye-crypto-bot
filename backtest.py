#!/usr/bin/env python3
"""
回測腳本 — 在歷史 K 線上跑 SMC 策略，輸出勝率與績效報告
用法: python backtest.py [--days 180]
"""
import time, argparse, bisect
from binance_client import BinanceClient
from strategy import StrategyEngine
from config import Config


# ── 資料下載 ──────────────────────────────────────────────────────────────────

def fetch_klines(client: BinanceClient, symbol: str, interval: str, days: int) -> list:
    """分批向前抓取歷史 K 線（最多 1500 根 / 次）"""
    limit     = 1500
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    all_klines: list = []
    end_time  = None

    while True:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time:
            params["endTime"] = end_time
        raw = client._get("/fapi/v1/klines", params)
        if not raw or isinstance(raw, dict):
            break
        all_klines = raw + all_klines
        oldest = raw[0][0]
        if oldest <= cutoff_ms:
            break
        end_time = oldest - 1
        time.sleep(0.15)  # 避免觸碰 rate limit

    return [k for k in all_klines if k[0] >= cutoff_ms]


# ── 模擬客戶端 ────────────────────────────────────────────────────────────────

class BacktestClient:
    """依 current_ts 截取預載 K 線，供 StrategyEngine 使用（不發真實 API 請求）"""

    def __init__(self, klines_4h: list, klines_1h: list, klines_15m: list):
        self._store = {
            '4h':  (klines_4h,  [k[0] for k in klines_4h]),
            '1h':  (klines_1h,  [k[0] for k in klines_1h]),
            '15m': (klines_15m, [k[0] for k in klines_15m]),
        }
        self.current_ts: int = 0

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        data, timestamps = self._store[interval]
        idx = bisect.bisect_right(timestamps, self.current_ts)
        return data[max(0, idx - limit): idx]


# ── 主回測邏輯 ────────────────────────────────────────────────────────────────

def run_backtest(days: int = 180):
    real_client = BinanceClient(Config.API_KEY, Config.SECRET_KEY)

    print(f"正在下載 {days} 天歷史資料（{Config.SYMBOL}）...")
    raw_4h  = fetch_klines(real_client, Config.SYMBOL, '4h',  days)
    raw_1h  = fetch_klines(real_client, Config.SYMBOL, '1h',  days)
    raw_15m = fetch_klines(real_client, Config.SYMBOL, '15m', days)
    print(f"  4H: {len(raw_4h)} 根 | 1H: {len(raw_1h)} 根 | 15m: {len(raw_15m)} 根")

    bt_client = BacktestClient(raw_4h, raw_1h, raw_15m)
    strategy  = StrategyEngine(bt_client, Config.SYMBOL)

    START_BALANCE = 1000.0
    balance       = START_BALANCE
    risk_pct      = Config.RISK_PERCENT

    trades: list = []
    in_trade      = False
    trade_entry   = trade_sl = trade_tp = 0.0
    trade_dir     = ''
    trade_balance = 0.0  # 進場時的帳戶餘額（用於計算定損金額）

    for kline in raw_15m:
        ts    = kline[0]
        high  = float(kline[2])
        low   = float(kline[3])
        close = float(kline[4])
        bt_client.current_ts = ts

        if in_trade:
            hit_sl, hit_tp = False, False
            if trade_dir == 'LONG':
                if low  <= trade_sl: hit_sl = True
                elif high >= trade_tp: hit_tp = True
            else:
                if high >= trade_sl: hit_sl = True
                elif low  <= trade_tp: hit_tp = True

            if hit_sl or hit_tp:
                risk_amt = trade_balance * risk_pct
                if hit_tp:
                    rr  = abs(trade_tp - trade_entry) / abs(trade_entry - trade_sl)
                    pnl = risk_amt * rr
                else:
                    pnl = -risk_amt
                balance += pnl
                trades.append({
                    'win': hit_tp, 'pnl': pnl,
                    'balance': balance, 'dir': trade_dir
                })
                in_trade = False
            continue  # 有倉位時（不管剛關還是持中）都不找新訊號

        # 無倉位，尋找訊號
        try:
            signal = strategy.get_signal()
        except Exception:
            continue
        if signal:
            sl_dist = abs(signal.entry - signal.stop_loss)
            if sl_dist == 0:
                continue
            in_trade      = True
            trade_entry   = signal.entry
            trade_sl      = signal.stop_loss
            trade_tp      = signal.take_profit
            trade_dir     = signal.direction
            trade_balance = balance

    # ── 統計輸出 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 52)
    print(f"  回測：{days} 天 | {Config.SYMBOL} | 起始本金 ${START_BALANCE:,.0f}")
    print("=" * 52)

    if not trades:
        print("  沒有觸發任何訊號，策略可能條件太嚴格")
        return

    wins         = [t for t in trades if t['win']]
    loses        = [t for t in trades if not t['win']]
    total        = len(trades)
    win_rate     = len(wins) / total * 100
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss   = abs(sum(t['pnl'] for t in loses))
    profit_factor = gross_profit / gross_loss if gross_loss else float('inf')
    total_return  = (balance - START_BALANCE) / START_BALANCE * 100

    # 最大回撤
    peak   = START_BALANCE
    max_dd = 0.0
    for t in trades:
        peak   = max(peak, t['balance'])
        max_dd = max(max_dd, (peak - t['balance']) / peak * 100)

    print(f"  總交易次數：{total} 筆（多 {sum(1 for t in trades if t['dir']=='LONG')} / 空 {sum(1 for t in trades if t['dir']=='SHORT')}）")
    print(f"  勝率：       {win_rate:.1f}%")
    print(f"  盈利因子：   {profit_factor:.2f}")
    print(f"  總報酬：     {total_return:+.1f}%")
    print(f"  最大回撤：   {max_dd:.1f}%")
    print(f"  最終餘額：   ${balance:,.2f}")
    print("-" * 52)

    if profit_factor < 1.0:
        print("  [!] 盈利因子 < 1.0 -> 策略長期虧損，建議優化後再上線")
    elif profit_factor < 1.5:
        print("  [!] 盈利因子 1.0~1.5 -> 小幅獲利但不夠穩定，注意滑點與手續費")
    else:
        print("  [OK] 盈利因子 >= 1.5 -> 策略有正期望值，可考慮上線")

    if max_dd > 20:
        print("  [!] 最大回撤超過 20%，槓桿風險高，建議縮小單筆倉位")
    print("=" * 52)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SMC 策略回測')
    parser.add_argument('--days', type=int, default=180, help='回測天數（預設 180）')
    args = parser.parse_args()
    run_backtest(args.days)
