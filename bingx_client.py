import hmac, hashlib, time, requests

class BingXClient:
    BASE = 'https://open-api.bingx.com'

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret

    def _sign(self, params: dict) -> str:
        qs = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        return hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _get(self, path: str, params: dict = {}) -> dict:
        p = {**params, 'timestamp': int(time.time() * 1000)}
        p['signature'] = self._sign(p)
        r = requests.get(f'{self.BASE}{path}', params=p,
                         headers={'X-BX-APIKEY': self.api_key}, timeout=10)
        return r.json()

    def _post(self, path: str, params: dict = {}) -> dict:
        p = {**params, 'timestamp': int(time.time() * 1000)}
        p['signature'] = self._sign(p)
        r = requests.post(f'{self.BASE}{path}', params=p,
                          headers={'X-BX-APIKEY': self.api_key}, timeout=10)
        return r.json()

    def get_balance(self) -> float:
        d = self._get('/openApi/swap/v2/user/balance')
        if 'data' not in d:
            raise ValueError(f'BingX balance API error: {d}')
        return float(d['data']['balance']['availableMargin'])

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        d = self._get('/openApi/swap/v3/quote/klines',
                      {'symbol': symbol, 'interval': interval, 'limit': limit})
        return d.get('data', [])

    def get_positions(self, symbol: str = None) -> list:
        params = {'symbol': symbol} if symbol else {}
        d = self._get('/openApi/swap/v2/user/positions', params)
        return [p for p in (d.get('data') or []) if float(p.get('positionAmt', 0)) != 0]

    def get_ticker(self, symbol: str) -> dict:
        d = requests.get(f'{self.BASE}/openApi/swap/v2/quote/ticker',
                         params={'symbol': symbol}, timeout=10).json()
        return d.get('data', {})

    def set_leverage(self, symbol: str, leverage: int, side: str) -> dict:
        return self._post('/openApi/swap/v2/trade/leverage',
                          {'symbol': symbol, 'side': side, 'leverage': leverage})

    def set_margin_type(self, symbol: str) -> dict:
        return self._post('/openApi/swap/v2/trade/marginType',
                          {'symbol': symbol, 'marginType': 'CROSSED'})

    def place_order(self, symbol: str, side: str, pos_side: str, qty: float) -> dict:
        return self._post('/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         side,
            'positionSide': pos_side,
            'type':         'MARKET',
            'quantity':     qty,
        })

    def place_tp_sl(self, symbol: str, pos_side: str, tp: float, sl: float):
        close_side = 'SELL' if pos_side == 'LONG' else 'BUY'
        self._post('/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': close_side, 'positionSide': pos_side,
            'type': 'TAKE_PROFIT_MARKET', 'stopPrice': tp, 'closePosition': 'true'
        })
        self._post('/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': close_side, 'positionSide': pos_side,
            'type': 'STOP_MARKET', 'stopPrice': sl, 'closePosition': 'true'
        })
