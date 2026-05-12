import schedule, time, traceback
from datetime import datetime
from config           import Config
from bingx_client     import BingXClient
from strategy         import StrategyEngine
from risk_manager     import RiskManager
from discord_notifier import DiscordNotifier

client   = BingXClient(Config.API_KEY, Config.SECRET_KEY)
strategy = StrategyEngine(client, Config.SYMBOL)
risk     = RiskManager(Config.RISK_PERCENT)
notifier = DiscordNotifier(Config.DISCORD_WEBHOOK)

def scan():
    try:
        now = datetime.now().strftime('%H:%M:%S')
        print(f'[{now}] 掃描中...')

        # 已有開倉就不再進新單
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

        # 設定槓桿 + 逐倉模式
        client.set_margin_type(Config.SYMBOL)
        client.set_leverage(Config.SYMBOL, Config.LEVERAGE, 'LONG')
        client.set_leverage(Config.SYMBOL, Config.LEVERAGE, 'SHORT')

        pos_side = 'LONG' if signal.direction == 'LONG' else 'SHORT'
        side     = 'BUY'  if signal.direction == 'LONG' else 'SELL'

        result = client.place_order(Config.SYMBOL, side, pos_side, order['qty'])

        if result.get('code') == 0:
            client.place_tp_sl(Config.SYMBOL, pos_side, signal.take_profit, signal.stop_loss)
            notifier.trade_open(signal, order, balance)
            print(f'[{now}] 下單成功')
        else:
            notifier.error(f'下單失敗：{result}')

    except Exception:
        err = traceback.format_exc()
        print(err)
        notifier.error(err)

def daily_report():
    try:
        balance = client.get_balance()
        notifier.daily_report(balance, 0, 0.0)
    except Exception:
        notifier.error(traceback.format_exc())

if __name__ == '__main__':
    print('本爺機器人啟動！')
    notifier.startup(client.get_balance())

    schedule.every(Config.SCAN_INTERVAL).minutes.do(scan)
    schedule.every().day.at(Config.DAILY_REPORT).do(daily_report)

    scan()  # 啟動時立即掃描一次

    while True:
        schedule.run_pending()
        time.sleep(1)
