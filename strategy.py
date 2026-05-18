import pandas as pd
from dataclasses import dataclass
from typing import Optional

@dataclass
class Signal:
    direction:   str    # 'LONG' or 'SHORT'
    entry:       float
    stop_loss:   float
    take_profit: float
    reason:      str

class StrategyEngine:
    def __init__(self, client, symbol: str = 'BTC-USDT'):
        self.client = client
        self.symbol = symbol

    # ── 資料解析 ──────────────────────────────────────────────────────────
    def _df(self, klines: list) -> pd.DataFrame:
        # 幣安 kline 回傳 list of arrays: [open_time, open, high, low, close, volume, ...]
        cols = ['time','open','high','low','close','volume',
                'close_time','qav','trades','tbbav','tbqav','ignore']
        df = pd.DataFrame(klines, columns=cols)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        return df.sort_values('time').reset_index(drop=True)

    # ── 擺動高低點 ────────────────────────────────────────────────────────
    def _swings(self, df: pd.DataFrame, w: int = 5) -> pd.DataFrame:
        df = df.copy()
        df['sh'] = df['high'].where(df['high'] == df['high'].rolling(w*2+1, center=True).max())
        df['sl'] = df['low'].where(df['low']  == df['low'].rolling(w*2+1, center=True).min())
        return df

    # ── 訂單塊偵測 ────────────────────────────────────────────────────────
    def _order_blocks(self, df: pd.DataFrame) -> list:
        obs = []
        for i in range(1, len(df) - 1):
            body_curr = abs(df.iloc[i]['close'] - df.iloc[i]['open'])
            body_next = abs(df.iloc[i+1]['close'] - df.iloc[i+1]['open'])
            strong    = body_next > body_curr * 1.5

            # 看漲 OB：當前收陰，下一根強陽
            if (df.iloc[i]['close'] < df.iloc[i]['open'] and
                df.iloc[i+1]['close'] > df.iloc[i+1]['open'] and strong):
                obs.append({'type':'bullish','high':df.iloc[i]['high'],'low':df.iloc[i]['low'],'i':i})

            # 看跌 OB：當前收陽，下一根強陰
            elif (df.iloc[i]['close'] > df.iloc[i]['open'] and
                  df.iloc[i+1]['close'] < df.iloc[i+1]['open'] and strong):
                obs.append({'type':'bearish','high':df.iloc[i]['high'],'low':df.iloc[i]['low'],'i':i})
        return obs[-15:]

    # ── 失衡區偵測 ────────────────────────────────────────────────────────
    def _fvg(self, df: pd.DataFrame) -> list:
        gaps = []
        for i in range(1, len(df) - 1):
            # 看漲 FVG
            if df.iloc[i-1]['high'] < df.iloc[i+1]['low']:
                gaps.append({'type':'bullish','high':df.iloc[i+1]['low'],'low':df.iloc[i-1]['high'],'i':i})
            # 看跌 FVG
            elif df.iloc[i-1]['low'] > df.iloc[i+1]['high']:
                gaps.append({'type':'bearish','high':df.iloc[i-1]['low'],'low':df.iloc[i+1]['high'],'i':i})
        return gaps[-15:]

    # ── 結構突破 BOS ──────────────────────────────────────────────────────
    def _bos(self, df: pd.DataFrame, direction: str) -> bool:
        s = self._swings(df, w=3)
        price = df.iloc[-1]['close']
        if direction == 'bullish':
            highs = s['sh'].dropna()
            return len(highs) >= 1 and price > highs.iloc[-1]
        else:
            lows = s['sl'].dropna()
            return len(lows) >= 1 and price < lows.iloc[-1]

    # ── 主訊號 ────────────────────────────────────────────────────────────
    def _check_direction(self, obs, fvgs, df_ref, df15m, price, label) -> Optional[Signal]:
        s = self._swings(df_ref)

        # 做多
        b_obs  = [o for o in obs  if o['type']=='bullish' and o['low'] <= price <= o['high']]
        b_fvgs = [f for f in fvgs if f['type']=='bullish' and f['low'] <= price <= f['high']]
        if (b_obs or b_fvgs) and self._bos(df15m, 'bullish'):
            zone = (b_obs or b_fvgs)[0]
            sl   = zone['low'] * 0.9995
            recent_high = s['sh'].dropna().tail(5).max()
            tp  = recent_high if pd.notna(recent_high) and recent_high > price else price * 1.008
            tag = 'OB+FVG' if b_obs and b_fvgs else ('OB' if b_obs else 'FVG')
            return Signal('LONG',  price, sl, tp, f'{label} {tag} + 15m BOS↑')

        # 做空
        s_obs  = [o for o in obs  if o['type']=='bearish' and o['low'] <= price <= o['high']]
        s_fvgs = [f for f in fvgs if f['type']=='bearish' and f['low'] <= price <= f['high']]
        if (s_obs or s_fvgs) and self._bos(df15m, 'bearish'):
            zone = (s_obs or s_fvgs)[0]
            sl   = zone['high'] * 1.0005
            recent_low = s['sl'].dropna().tail(5).min()
            tp  = recent_low if pd.notna(recent_low) and recent_low < price else price * 0.992
            tag = 'OB+FVG' if s_obs and s_fvgs else ('OB' if s_obs else 'FVG')
            return Signal('SHORT', price, sl, tp, f'{label} {tag} + 15m BOS↓')

        return None

    def get_signal(self) -> Optional[Signal]:
        df4h  = self._df(self.client.get_klines(self.symbol, '4h',  100))
        df1h  = self._df(self.client.get_klines(self.symbol, '1h',  100))
        df15m = self._df(self.client.get_klines(self.symbol, '15m', 60))
        price = df15m.iloc[-1]['close']

        # 優先用 4H（高質量）
        signal = self._check_direction(
            self._order_blocks(df4h), self._fvg(df4h), df4h, df15m, price, '4H'
        )
        if signal:
            return signal

        # 沒有 4H 就用 1H
        signal = self._check_direction(
            self._order_blocks(df1h), self._fvg(df1h), df1h, df15m, price, '1H'
        )
        return signal
