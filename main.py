import schedule, time, traceback, json, os
from datetime import datetime, date
from config           import Config
from binance_client   import BinanceClient
from strategy         import StrategyEngine
from risk_manager     import RiskManager
from discord_notifier import DiscordNotifier
import analyst

client   = BinanceClient(Config.API_KEY, Config.SECRET_KEY)
strategy = StrategyEngine(client, Config.SYMBOL)
risk     = RiskManager(Config.RISK_PERCENT)
notifier = DiscordNotifier(Config.DISCORD_WEBHOOK)

STATE_FILE = 'state.json'

def load_state() -> dict:
    """載入今日統計，跨天自動重置"""
    today = str(date.today())
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        if s.get('date') == today:
            return s
    # 新的一天，用當前餘額當起點
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

def scan():
    try:
        now = datetime.now().strftime('%H:%M:%S')
        print(f'[{now}] 掃描中...')

        if client.get_positions(Config.SYMBOL):
            print(f'[{now}] 已有倉位，略過')
            return

        signal = strategy.get_signal()
        if not signal:
            print(f'[{now}] 無訊號')
            return

        print(f'[{now}] 訊號：{signal.direction} @ {signal.entry:.2f}')

        balance = client.get_balance()
        order   = risk.calc(signal, balance)

        if Config.SIMULATION:
            notifier.trade_open(signal, order, balance, leverage=Config.LEVERAGE, simulation=True)
            print(f'[{now}] 【模擬】訊號通知已發送，未下單')
            return

        client.set_margin_type(Config.SYMBOL)
        client.set_leverage(Config.SYMBOL, Config.LEVERAGE, 'LONG')
        client.set_leverage(Config.SYMBOL, Config.LEVERAGE, 'SHORT')

        pos_side = 'LONG' if signal.direction == 'LONG' else 'SHORT'
        side     = 'BUY'  if signal.direction == 'LONG' else 'SELL'

        result = client.place_order(Config.SYMBOL, side, pos_side, order['qty'])

        if 'orderId' in result:
            client.place_tp_sl(Config.SYMBOL, pos_side,
                               signal.take_profit, signal.stop_loss)
            notifier.trade_open(signal, order, balance, leverage=Config.LEVERAGE)
            # 記錄這筆交易
            s = load_state()
            s['trades'] += 1
            _save_state(s)
            print(f'[{now}] 下單成功（今日第 {s["trades"]} 筆）')
        else:
            notifier.error(f'下單失敗：{result}')

    except Exception:
        err = traceback.format_exc()
        print(err)
        notifier.error(err)

def daily_report():
    try:
        balance = client.get_balance()
        s       = load_state()
        pnl     = balance - s['start_balance']
        trades  = s['trades']
        notifier.daily_report(balance, trades, pnl)

        # 策略分析師：同步新PnL並發分析報告
        analyst.sync(client)
        notifier.analyst_report(analyst.analyze())

        # 重置隔天起點
        new_s = {'date': str(date.today()), 'start_balance': balance, 'trades': 0}
        _save_state(new_s)
    except Exception:
        notifier.error(traceback.format_exc())

if __name__ == '__main__':
    try:
        print('本爺機器人啟動！')
        balance = client.get_balance()
        notifier.startup(balance)
    except Exception:
        err = traceback.format_exc()
        print(f'啟動失敗：\n{err}')
        raise SystemExit(1)

    schedule.every(Config.SCAN_INTERVAL).minutes.do(scan)
    schedule.every().day.at(Config.DAILY_REPORT).do(daily_report)

    scan()  # 啟動時立即掃描一次

    while True:
        schedule.run_pending()
        time.sleep(1)
