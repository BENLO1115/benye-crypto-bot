import schedule, time, traceback, json, os, logging
from datetime import datetime, date
from logging.handlers import RotatingFileHandler
from config           import Config
from binance_client   import BinanceClient
from strategy         import StrategyEngine, Signal
from risk_manager     import RiskManager
from discord_notifier import DiscordNotifier
import analyst

# ── 持久化日誌（同時輸出到 Railway console 和本地 bot.log）──────────────────
_fmt     = logging.Formatter('%(asctime)s  %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
_fh      = RotatingFileHandler('bot.log', maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')
_fh.setFormatter(_fmt)
_sh      = logging.StreamHandler()
_sh.setFormatter(_fmt)
log      = logging.getLogger('bot')
log.setLevel(logging.INFO)
log.addHandler(_fh)
log.addHandler(_sh)

client   = BinanceClient(Config.API_KEY, Config.SECRET_KEY)
strategy = StrategyEngine(client, Config.SYMBOL)
risk     = RiskManager(Config.RISK_PERCENT)
notifier = DiscordNotifier(Config.DISCORD_WEBHOOK)

STATE_FILE    = 'state.json'
PENDING_FILE  = 'pending.json'
POSITION_FILE = 'position.json'


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


# ── 倉位狀態管理 ──────────────────────────────────────────────────────────────

def load_position() -> dict:
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE) as f:
            return json.load(f)
    return {}

def save_position(pos_side: str, tp: float, sl: float):
    with open(POSITION_FILE, 'w') as f:
        json.dump({'symbol': Config.SYMBOL, 'pos_side': pos_side,
                   'tp': tp, 'sl': sl}, f)

def clear_position():
    if os.path.exists(POSITION_FILE):
        os.remove(POSITION_FILE)


# ── TP/SL 驗證（下單後確認有掛上去）──────────────────────────────────────────

def _verify_tp_sl(pos_side: str, tp: float, sl: float) -> bool:
    open_orders = client.get_open_orders(Config.SYMBOL)
    has_tp = any(o.get('type') == 'TAKE_PROFIT_MARKET' and
                 o.get('positionSide') == pos_side for o in open_orders)
    has_sl = any(o.get('type') == 'STOP_MARKET' and
                 o.get('positionSide') == pos_side for o in open_orders)

    if not has_tp or not has_sl:
        missing = []
        if not has_tp: missing.append('止盈')
        if not has_sl: missing.append('止損')
        # 重新掛一次
        client.place_tp_sl(Config.SYMBOL, pos_side, tp, sl)
        notifier.error(f'TP/SL 缺失（{" / ".join(missing)}），已自動補掛')
        return False
    return True


# ── 開機倉位恢復 ──────────────────────────────────────────────────────────────

def _recover_position():
    positions = client.get_positions(Config.SYMBOL)
    if not positions:
        clear_position()
        return

    pos_data = load_position()
    if not pos_data:
        # 有倉位但沒有記錄，無法自動恢復，通知人工處理
        notifier.error('偵測到未追蹤倉位，請手動確認幣安 TP/SL 是否設定正確')
        return

    # 有記錄，驗證 TP/SL
    ok = _verify_tp_sl(pos_data['pos_side'], pos_data['tp'], pos_data['sl'])
    status = 'TP/SL 正常' if ok else 'TP/SL 已補掛'
    log.info(f'[啟動] 恢復倉位 {pos_data["pos_side"]} — {status}')


# ── 處理掛單 ──────────────────────────────────────────────────────────────────

def _handle_pending(pending: dict, now: str, balance: float):
    order  = client.get_order(pending['symbol'], pending['orderId'])
    status = order.get('status', '')

    if status == 'FILLED':
        client.place_tp_sl(pending['symbol'], pending['pos_side'],
                           pending['tp'], pending['sl'])
        # 驗證 TP/SL 有掛上
        _verify_tp_sl(pending['pos_side'], pending['tp'], pending['sl'])
        # 儲存倉位記錄
        save_position(pending['pos_side'], pending['tp'], pending['sl'])

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
        log.info(f'[{now}] LIMIT單成交，TP/SL 已確認，今日第 {s["trades"]} 筆')
        clear_pending()

    elif status in ('CANCELED', 'REJECTED', 'EXPIRED'):
        log.info(f'[{now}] 掛單已取消/失效，清除')
        clear_pending()

    else:
        elapsed = int(time.time()) - pending.get('placed_ts', 0)
        if elapsed > Config.LIMIT_EXPIRY_MIN * 60:
            client.cancel_order(pending['symbol'], pending['orderId'])
            log.info(f'[{now}] 掛單超時 {Config.LIMIT_EXPIRY_MIN} 分鐘，已取消')
            clear_pending()
        else:
            mins, secs = divmod(elapsed, 60)
            log.info(f'[{now}] 等待LIMIT成交... ({mins}分{secs}秒)')


# ── 主掃描邏輯 ────────────────────────────────────────────────────────────────

def scan():
    try:
        now = datetime.now().strftime('%H:%M:%S')
        log.info(f'[{now}] 掃描中...')

        if Config.PAUSE_TRADING:
            log.info(f'[{now}] 暫停交易中（PAUSE_TRADING=true）')
            return

        balance = client.get_balance()
        s       = load_state()

        # 每日最大虧損
        if s['start_balance'] > 0:
            daily_pnl_pct = (balance - s['start_balance']) / s['start_balance']
            if daily_pnl_pct < -Config.MAX_DAILY_LOSS_PCT:
                log.info(f'[{now}] 今日虧損 {daily_pnl_pct*100:.1f}%，已達上限，停止當日交易')
                return

        # 處理現有掛單
        pending = load_pending()
        if pending:
            _handle_pending(pending, now, balance)
            return

        # 檢查持倉
        positions = client.get_positions(Config.SYMBOL)
        pos_data  = load_position()

        if positions:
            if pos_data:
                # 正常持倉中，順帶驗證 TP/SL
                _verify_tp_sl(pos_data['pos_side'], pos_data['tp'], pos_data['sl'])
            else:
                notifier.error('偵測到未追蹤倉位，請手動確認幣安 TP/SL')
            log.info(f'[{now}] 已有倉位，略過')
            return
        elif pos_data:
            # 倉位已平，清除紀錄
            clear_position()
            log.info(f'[{now}] 倉位已平，清除記錄')

        # 找訊號
        signal = strategy.get_signal(Config.MIN_RR)
        if not signal:
            log.info(f'[{now}] 無訊號')
            return

        log.info(f'[{now}] 訊號：{signal.direction} @ {signal.entry:.2f} | {signal.reason}')
        order_info = risk.calc(signal, balance)

        if Config.SIMULATION:
            notifier.trade_open(signal, order_info, balance,
                                leverage=Config.LEVERAGE, simulation=True)
            log.info(f'[{now}] 【模擬】訊號通知已發送，未下單')
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
            log.info(f'[{now}] LIMIT掛單成功：{side} {order_info["qty"]} BTC @ {signal.entry:.2f}')
        else:
            notifier.error(f'掛單失敗：{result}')

    except Exception:
        err = traceback.format_exc()
        log.error(err)
        notifier.error(err)


# ── 每日報告 ──────────────────────────────────────────────────────────────────

def _check_api_key_expiry():
    if not Config.API_KEY_DATE:
        return
    try:
        from datetime import timedelta
        key_date = datetime.strptime(Config.API_KEY_DATE, '%Y-%m-%d').date()
        expiry   = key_date + timedelta(days=90)
        days_left = (expiry - date.today()).days
        if 0 <= days_left <= 2:
            notifier.alert(
                'API 金鑰即將到期',
                f'幣安 API 金鑰將於 **{days_left} 天後**（{expiry}）到期。\n'
                f'請前往幣安 → API 管理 → 重新建立金鑰，並更新 Railway 環境變數。'
            )
    except Exception:
        pass

def daily_report():
    try:
        balance      = client.get_balance()
        funding_rate = client.get_funding_rate(Config.SYMBOL)
        s            = load_state()
        pnl          = balance - s['start_balance']
        notifier.daily_report(balance, s['trades'], pnl, funding_rate)

        analyst.sync(client)
        notifier.analyst_report(analyst.analyze())
        _check_api_key_expiry()

        new_s = {'date': str(date.today()), 'start_balance': balance, 'trades': 0}
        _save_state(new_s)
    except Exception:
        notifier.error(traceback.format_exc())


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    try:
        log.info('本爺機器人啟動！')
        balance      = client.get_balance()
        funding_rate = client.get_funding_rate(Config.SYMBOL)
        _recover_position()  # 恢復倉位狀態
        notifier.startup(balance, funding_rate)
    except Exception:
        err = traceback.format_exc()
        log.error(f'啟動失敗：\n{err}')
        raise SystemExit(1)

    schedule.every(Config.SCAN_INTERVAL).minutes.do(scan)
    schedule.every().day.at(Config.DAILY_REPORT).do(daily_report)

    scan()

    while True:
        schedule.run_pending()
        time.sleep(1)
