import hmac, hashlib, time, requests
from urllib.parse import urlencode

class BinanceClient:
    BASE    = "https://fapi.binance.com"
    RETRIES = 3

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret

    def _sign(self, qs: str) -> str:
        return hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: dict) -> any:
        p  = {**params, "timestamp": int(time.time() * 1000)}
        qs = urlencode(p)
        sig = self._sign(qs)
        url = f"{self.BASE}{path}?{qs}&signature={sig}"
        hdrs = {"X-MBX-APIKEY": self.api_key}
        for attempt in range(self.RETRIES):
            try:
                if method == 'GET':
                    r = requests.get(url, headers=hdrs, timeout=10)
                else:
                    r = requests.post(url, headers=hdrs, timeout=10)
                return r.json()
            except requests.exceptions.RequestException:
                if attempt == self.RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)

    def _get(self, path: str, params: dict = {}) -> any:
        return self._request('GET', path, params)

    def _post(self, path: str, params: dict = {}) -> dict:
        return self._request('POST', path, params)

    def get_balance(self) -> float:
        d = self._get("/fapi/v2/balance")
        if isinstance(d, dict):
            raise ValueError(f"Binance balance API error: {d}")
        for asset in d:
            if asset["asset"] == "USDT":
                return float(asset["availableBalance"])
        raise ValueError("USDT balance not found")

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        return self._get("/fapi/v1/klines",
                         {"symbol": symbol, "interval": interval, "limit": limit})

    def get_positions(self, symbol: str = None) -> list:
        params = {"symbol": symbol} if symbol else {}
        d = self._get("/fapi/v2/positionRisk", params)
        if isinstance(d, dict):
            return []
        return [p for p in d if float(p.get("positionAmt", 0)) != 0]

    def get_order(self, symbol: str, order_id: int) -> dict:
        return self._get("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._post("/fapi/v1/order/cancel" if False else "/fapi/v1/order",
                          {"symbol": symbol, "orderId": order_id})

    def get_funding_rate(self, symbol: str) -> float:
        d = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(d.get("lastFundingRate", 0)) * 100 if isinstance(d, dict) else 0.0

    def get_income_history(self, income_type: str = 'REALIZED_PNL', limit: int = 50) -> list:
        result = self._get('/fapi/v1/income', {
            'incomeType': income_type,
            'limit':      limit,
        })
        return result if isinstance(result, list) else []

    def set_leverage(self, symbol: str, leverage: int, side: str = None) -> dict:
        return self._post("/fapi/v1/leverage",
                          {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol: str) -> dict:
        d = self._post("/fapi/v1/marginType",
                       {"symbol": symbol, "marginType": "CROSSED"})
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

    def place_limit_order(self, symbol: str, side: str, pos_side: str,
                          qty: float, price: float) -> dict:
        return self._post("/fapi/v1/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "LIMIT",
            "price":        price,
            "quantity":     qty,
            "timeInForce":  "GTC",
        })

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._post("/fapi/v1/order", {
            "symbol":  symbol,
            "orderId": order_id,
        })

    def place_tp_sl(self, symbol: str, pos_side: str, tp: float, sl: float):
        close_side = "SELL" if pos_side == "LONG" else "BUY"

        self._post("/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "positionSide": pos_side,
            "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 1),
            "closePosition": "true"
        })

        self._post("/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "positionSide": pos_side,
            "type": "STOP_MARKET", "stopPrice": round(sl, 1),
            "closePosition": "true"
        })
