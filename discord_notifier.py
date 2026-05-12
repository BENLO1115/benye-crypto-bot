import requests
from datetime import datetime

class DiscordNotifier:
    def __init__(self, webhook: str):
        self.webhook = webhook

    def _send(self, embed: dict):
        requests.post(self.webhook, json={'embeds': [embed]}, timeout=10)

    def trade_open(self, signal, order: dict, balance: float, simulation: bool = False):
        emoji = '🟢' if signal.direction == 'LONG' else '🔴'
        color = 0x2ecc71 if signal.direction == 'LONG' else 0xe74c3c
        title = f'{"🧪 【模擬】" if simulation else ""}{emoji} 進場 — {signal.direction}'
        self._send({
            'title': title,
            'color': color,
            'fields': [
                {'name': '進場價',   'value': f'`${signal.entry:,.2f}`',       'inline': True},
                {'name': '止損',     'value': f'`${signal.stop_loss:,.2f}`',   'inline': True},
                {'name': '止盈',     'value': f'`${signal.take_profit:,.2f}`', 'inline': True},
                {'name': '倉位',     'value': f'`{order["qty"]} BTC`',         'inline': True},
                {'name': '風險金額', 'value': f'`${order["risk_amt"]:.2f}`',   'inline': True},
                {'name': 'R:R',      'value': f'`1:{order["rr"]}`',            'inline': True},
                {'name': '保證金',   'value': f'`${order["margin"]:.2f}`',     'inline': True},
                {'name': '帳戶餘額', 'value': f'`${balance:.2f}`',             'inline': True},
                {'name': '策略依據', 'value': signal.reason,                   'inline': False},
            ],
            'footer': {'text': f'本爺機器人 • {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'}
        })

    def daily_report(self, balance: float, trades_today: int, pnl_today: float):
        emoji = '📈' if pnl_today >= 0 else '📉'
        self._send({
            'title': f'{emoji} 每日報告',
            'color': 0x3498db,
            'fields': [
                {'name': '帳戶餘額',   'value': f'`${balance:.2f} USDT`', 'inline': True},
                {'name': '今日交易數', 'value': f'`{trades_today} 筆`',   'inline': True},
                {'name': '今日損益',   'value': f'`${pnl_today:+.2f}`',   'inline': True},
            ],
            'footer': {'text': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        })

    def error(self, msg: str):
        self._send({
            'title': '⚠️ 機器人錯誤',
            'color': 0xe74c3c,
            'description': f'```{str(msg)[:1000]}```',
            'footer': {'text': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        })

    def startup(self, balance: float):
        self._send({
            'title': '🚀 本爺機器人啟動',
            'color': 0x9b59b6,
            'fields': [{'name': '帳戶餘額', 'value': f'`${balance:.2f} USDT`', 'inline': True}],
            'footer': {'text': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        })
