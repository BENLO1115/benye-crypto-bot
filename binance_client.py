import hmac, hashlib, time, requests
from urllib.parse import urlencode

class BinanceClient:
    BASE = "https://fapi.binance.com"

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret

    def _sign(self, qs: str) -> str:
        return hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _get(self, path: str, params: dict = {}) -> any:
        p = {**params, "timestamp": int(time.time() * 1000)}
        qs = urlencode(p)
        sig = self._sign(qs)
        r = requests.get(f"{self.BASE}{path}?{qs}&signature={sig}",
                         headers={"X-MBX-APIKEY": self.api_key}, timeout=10)
        return r.json()

    def _post(self, path: str, params: dict = {}) -> dict:
        p = {**params, "timestamp": int(time.time() * 1000)}
        qs = urlencode(p)
        sig = self._sign(qs)
        r = requests.post(f"{self.BASE}{path}?{qs}&signature={sig}",
                          headers={"X-MBX-APIKEY": self.api_key}, timeout=10)
        return r.json()

    def get_balance(self) -> float:
        d = self._get("/fapi/v2/balance")
        if isinstance(d, dict):
            raise ValueError(f"Binance balance API error: {d}")
        for asset in d:
            if asset["asset"] == "USDT":
                return float(asset["availableBalance"])
        raise ValueError("USDT balance not found")

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        # 回傳格式：list of [open_time, open, high, low, close, volume, ...]
        return self._get("/fapi/v1/klines",
                         {"symbol": symbol, "interval": interval, "limit": limit})

    def get_positions(self, symbol: str = None) -> list:
        params = {"symbol": symbol} if symbol else {}
        d = self._get("/fapi/v2/positionRisk", params)
        if isinstance(d, dict):
            return []
        return [p for p in d if float(p.get("positionAmt", 0)) != 0]

    def get_ticker(self, symbol: str) -> dict:
        r = requests.get(f"{self.BASE}/fapi/v1/ticker/price",
                         params={"symbol": symbol}, timeout=10)
        return r.json()

    def set_leverage(self, symbol: str, leverage: int, side: str = None) -> dict:
        # 幣安不分多空設槓桿，side 參數保留相容性但不使用
        return self._post("/fapi/v1/leverage",
                          {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol: str) -> dict:
        d = self._post("/fapi/v1/marginType",
                       {"symbol": symbol, "marginType": "CROSSED"})
        # code -4046 = 已經是全倉模式，視為成功
        if isinstance(d, dict) and d.get("code") == -4046:
            return {"msg": "already CROSSED"}
        return d

    def place_order(self, symbol: str, side: str, pos_side: str, qty: float) -> dict:
        return self._post("/fapi/v1/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     qty,
        })

    def place_tp_sl(self, symbol: str, pos_side: str, entry: float, tp: float, sl: float):
        close_side = "SELL" if pos_side == "LONG" else "BUY"

        # 固定止盈
        self._post("/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "positionSide": pos_side,
            "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 1),
            "closePosition": "true"
        })

        # 移動止損：callback rate = SL 距離%，幣安限制 0.1~5.0
        sl_pct   = abs(entry - sl) / entry * 100
        callback = max(min(round(sl_pct, 1), 5.0), 0.1)
        self._post("/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "positionSide": pos_side,
            "type": "TRAILING_STOP_MARKET",
            "callbackRate": callback,
            "closePosition": "true"
        })
