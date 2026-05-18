import schedule, time, traceback, json, os
from datetime import datetime, date
from config           import Config
from binance_client   import BinanceClient
from strategy         import StrategyEngine, Signal
from risk_manager     import RiskManager
from discord_notifier import DiscordNotifier
import analyst

client   = BinanceClient(Config.API_KEY, Config.SECRET_KEY)
strategy = StrategyEngine(client, Config.SYMBOL)
risk     = RiskManager(Config.RISK_PERCENT)
notifier = DiscordNotifier(Config.DISCORD_WEBHOOK)

STATE_FILE   = 'state.json'
PENDING_FILE = 'pending.json'


# ── 每日狀態 ──────────────────────────────────────────────────────────────────

def load_state() -> dict:
    today = str(date.today())
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        if s.get('date') == today:
            return s
    try:
        bal = client.get_balance()
    except Exception:
        bal = 0.0
    s = {'date': today, 'start_balance': bal, 'trades': 0}
    _save_state(s)
    return s

def _save_state(s: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f)


# ── Limit 掛單管理 ────────────────────────────────────────────────────────────

def load_pending() -> dict:
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE) as f:
            return json.load(f)
    return {}

def save_pending(p: dict):
    with open(PENDING_FILE, 'w') as f:
        json.dump(p, f)

def clear_pending():
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)

def _handle_pending(pending: dict, now: str, balance: float):
    """檢查掛出去的 Limit 單是否成交"""
    order = client.get_order(pending['symbol'], pending['orderId'])
    status = order.get('status', '')

    if status == 'FILLED':
        client.place_tp_sl(pending['symbol'], pending['pos_side'],
                           pending['tp'], pending['sl'])
        sig = Signal(
            direction   = pending['direction'],
            entry       = pending['entry'],
            stop_loss   = pending['sl'],
            take_profit = pending['tp'],
            reason      = pending['reason'],
        )
        notifier.trade_open(sig, pending['order_info'], balance, leverage=Config.LEVERAGE)
        s = load_state()
        s['trades'] += 1
        _save_state(s)
        print(f'[{now}] LIMIT單成交，今日第 {s["trades"]} 筆')
        clear_pending()

    elif status in ('CANCELED', 'REJECTED', 'EXPIRED'):
        print(f'[{now}] 掛單已取消/失效，清除')
        clear_pending()

    else:
        elapsed = int(time.time()) - pending.get('placed_ts', 0)
        if elapsed > Config.LIMIT_EXPIRY_MIN * 60:
            client.cancel_order(pending['symbol'], pending['orderId'])
            print(f'[{now}] 掛單超時 {Config.LIMIT_EXPIRY_MIN} 分鐘，已取消')
            clear_pending()
        else:
            mins, secs = divmod(elapsed, 60)
            print(f'[{now}] 等待LIMIT成交... ({mins}分{secs}秒)')


# ── 主掃描邏輯 ────────────────────────────────────────────────────────────────

def scan():
    try:
        now = datetime.now().strftime('%H:%M:%S')
        print(f'[{now}] 掃描中...')

        # 手動暫停開關
        if Config.PAUSE_TRADING:
            print(f'[{now}] 暫停交易中（PAUSE_TRADING=true）')
            return

        balance = client.get_balance()
        s       = load_state()

        # 每日最大虧損檢查
        if s['start_balance'] > 0:
            daily_pnl_pct = (balance - s['start_balance']) / s['start_balance']
            if daily_pnl_pct < -Config.MAX_DAILY_LOSS_PCT:
                print(f'[{now}] 今日虧損 {daily_pnl_pct*100:.1f}%，已達上限，停止當日交易')
                return

        # 處理現有掛單
        pending = load_pending()
        if pending:
            _handle_pending(pending, now, balance)
            return

        # 已有持倉
        if client.get_positions(Config.SYMBOL):
            print(f'[{now}] 已有倉位，略過')
            return

        # 找訊號
        signal = strategy.get_signal(Config.MIN_RR)
        if not signal:
            print(f'[{now}] 無訊號')
            return

        print(f'[{now}] 訊號：{signal.direction} @ {signal.entry:.2f} | {signal.reason}')
        order_info = risk.calc(signal, balance)

        if Config.SIMULATION:
            notifier.trade_open(signal, order_info, balance,
                                leverage=Config.LEVERAGE, simulation=True)
            print(f'[{now}] 【模擬】訊號通知已發送，未下單')
            return

        client.set_margin_type(Config.SYMBOL)
        client.set_leverage(Config.SYMBOL, Config.LEVERAGE)

        pos_side = 'LONG' if signal.direction == 'LONG' else 'SHORT'
        side     = 'BUY'  if signal.direction == 'LONG' else 'SELL'

        result = client.place_limit_order(Config.SYMBOL, side, pos_side,
                                          order_info['qty'], signal.entry)

        if 'orderId' in result:
            save_pending({
                'orderId':    result['orderId'],
                'symbol':     Config.SYMBOL,
                'pos_side':   pos_side,
                'direction':  signal.direction,
                'entry':      signal.entry,
                'tp':         signal.take_profit,
                'sl':         signal.stop_loss,
                'reason':     signal.reason,
                'order_info': order_info,
                'placed_ts':  int(time.time()),
            })
            print(f'[{now}] LIMIT掛單成功：{side} {order_info["qty"]} BTC @ {signal.entry:.2f}')
        else:
            notifier.error(f'掛單失敗：{result}')

    except Exception:
        err = traceback.format_exc()
        print(err)
        notifier.error(err)


# ── 每日報告 ──────────────────────────────────────────────────────────────────

def daily_report():
    try:
        balance      = client.get_balance()
        funding_rate = client.get_funding_rate(Config.SYMBOL)
        s            = load_state()
        pnl          = balance - s['start_balance']
        notifier.daily_report(balance, s['trades'], pnl, funding_rate)

        analyst.sync(client)
        notifier.analyst_report(analyst.analyze())

        new_s = {'date': str(date.today()), 'start_balance': balance, 'trades': 0}
        _save_state(new_s)
    except Exception:
        notifier.error(traceback.format_exc())


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    try:
        print('本爺機器人啟動！')
        balance      = client.get_balance()
        funding_rate = client.get_funding_rate(Config.SYMBOL)
        notifier.startup(balance, funding_rate)
    except Exception:
        err = traceback.format_exc()
        print(f'啟動失敗：\n{err}')
        raise SystemExit(1)

    schedule.every(Config.SCAN_INTERVAL).minutes.do(scan)
    schedule.every().day.at(Config.DAILY_REPORT).do(daily_report)

    scan()

    while True:
        schedule.run_pending()
        time.sleep(1)
