"""
策略分析師 — 自動同步幣安已實現損益，偵測策略問題並提出優化建議
"""
import json, os
from binance_client import BinanceClient

HISTORY_FILE = 'trades_history.json'
WINDOW       = 20   # 分析最近 N 筆
MIN_SAMPLE   = 8    # 不足此筆數不分析

THRESHOLDS = {
    'consec_loss':   3,     # 連敗超過幾筆觸發警報
    'min_win_rate':  0.38,  # 勝率低於此值觸發警報
    'min_pf':        1.0,   # 盈利因子低於此值觸發警報
}


def _load() -> list:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return []


def _save(trades: list):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(trades, f, indent=2)


def sync(client: BinanceClient) -> int:
    """從幣安抓取最新已實現損益，去重後存檔，回傳新增筆數"""
    records = client.get_income_history('REALIZED_PNL', limit=50)
    if not records:
        return 0

    trades      = _load()
    existing_ids = {t['id'] for t in trades}
    new_count   = 0

    for r in records:
        tid = str(r.get('tranId', r.get('time', '')))
        if tid in existing_ids:
            continue
        pnl = float(r.get('income', 0))
        if pnl == 0:
            continue  # 手續費、資金費率等雜項排除
        trades.append({
            'id':     tid,
            'time':   int(r.get('time', 0)),
            'pnl':    pnl,
            'win':    pnl > 0,
            'symbol': r.get('symbol', ''),
        })
        new_count += 1

    if new_count:
        trades.sort(key=lambda x: x['time'])
        _save(trades)

    return new_count


def analyze() -> dict:
    """分析最近 WINDOW 筆交易，回傳統計與問題診斷"""
    all_trades = _load()
    recent     = all_trades[-WINDOW:] if len(all_trades) >= MIN_SAMPLE else all_trades

    if len(recent) < MIN_SAMPLE:
        return {
            'status':  'no_data',
            'message': f'交易紀錄 {len(recent)} 筆，需累積至 {MIN_SAMPLE} 筆才開始分析',
            'total':   len(recent),
        }

    wins   = [t for t in recent if t['win']]
    losses = [t for t in recent if not t['win']]
    total  = len(recent)

    win_rate     = len(wins) / total
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss   = abs(sum(t['pnl'] for t in losses)) or 0.001
    profit_factor = gross_profit / gross_loss

    # 計算連敗（從最新往回數）
    consec_loss = 0
    for t in reversed(recent):
        if not t['win']:
            consec_loss += 1
        else:
            break

    issues      = []
    suggestions = []

    if consec_loss >= THRESHOLDS['consec_loss']:
        issues.append(f'連敗 {consec_loss} 筆')
        suggestions.append('暫停 1 根K線冷靜期，避免在趨勢不明時硬進')

    if win_rate < THRESHOLDS['min_win_rate']:
        issues.append(f'勝率 {win_rate*100:.0f}%（門檻 {THRESHOLDS["min_win_rate"]*100:.0f}%）')
        suggestions.append('OB/FVG 確認條件加嚴，只取結構清晰、BOS 明確的訊號')

    if profit_factor < THRESHOLDS['min_pf']:
        issues.append(f'盈利因子 {profit_factor:.2f}（門檻 {THRESHOLDS["min_pf"]}）')
        suggestions.append('確保每筆止盈距離至少是止損的 1.5 倍，避免小勝大輸')

    return {
        'status':        'warning' if issues else 'healthy',
        'total':         total,
        'win_rate':      win_rate,
        'profit_factor': profit_factor,
        'consec_loss':   consec_loss,
        'issues':        issues,
        'suggestions':   suggestions,
    }
