import pandas as pd
from dataclasses import dataclass
from typing import Optional

@dataclass
class Signal:
    direction:   str
    entry:       float
    stop_loss:   float
    take_profit: float
    reason:      str

class StrategyEngine:
    def __init__(self, client, symbol: str = 'BTCUSDT'):
        self.client = client
        self.symbol = symbol

    # ── 資料解析 ──────────────────────────────────────────────────────────────
    def _df(self, klines: list) -> pd.DataFrame:
        cols = ['time','open','high','low','close','volume',
                'close_time','qav','trades','tbbav','tbqav','ignore']
        df = pd.DataFrame(klines, columns=cols)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        return df.sort_values('time').reset_index(drop=True)

    # ── 擺動高低點 ────────────────────────────────────────────────────────────
    def _swings(self, df: pd.DataFrame, w: int = 5) -> pd.DataFrame:
        df = df.copy()
        df['sh'] = df['high'].where(df['high'] == df['high'].rolling(w*2+1, center=True).max())
        df['sl'] = df['low'].where(df['low']  == df['low'].rolling(w*2+1, center=True).min())
        return df

    # ── 流動性池（近期擺動高低點 = 散戶止損聚集地）────────────────────────────
    def _liquidity_levels(self, df: pd.DataFrame) -> dict:
        s = self._swings(df, w=5)
        highs = s['sh'].dropna().tail(8).tolist()
        lows  = s['sl'].dropna().tail(8).tolist()
        return {'highs': highs, 'lows': lows}

    # ── 流動性掃除偵測 ────────────────────────────────────────────────────────
    def _swept_liquidity(self, df: pd.DataFrame, levels: list, direction: str) -> Optional[float]:
        """
        近10根K線是否掃過流動性後收回：
        bull → 假跌破低點（wick破下、收回上方）→ 看漲逆轉機會
        bear → 假突破高點（wick破上、收回下方）→ 看跌逆轉機會
        回傳被掃的流動性水位
        """
        recent = df.tail(10)
        for level in levels:
            for _, c in recent.iterrows():
                if direction == 'bull' and c['low'] < level and c['close'] > level:
                    return level
                if direction == 'bear' and c['high'] > level and c['close'] < level:
                    return level
        return None

    # ── 位移確認（機構力道）──────────────────────────────────────────────────
    def _has_displacement(self, df: pd.DataFrame, direction: str) -> bool:
        """
        近5根K線是否出現大實體K棒（實體 >= 55% 總幅度）
        代表機構入場，市場失衡，方向明確
        """
        for _, c in df.tail(5).iterrows():
            body  = abs(c['close'] - c['open'])
            total = c['high'] - c['low']
            if total == 0:
                continue
            if direction == 'bull' and c['close'] > c['open'] and body / total >= 0.55:
                return True
            if direction == 'bear' and c['close'] < c['open'] and body / total >= 0.55:
                return True
        return False

    # ── 區間拒絕確認（採到關鍵區間後的結構反應）────────────────────────────────
    def _zone_rejection(self, df: pd.DataFrame, zone: dict, direction: str) -> bool:
        """
        價格進入 OB/FVG 後，近3根K線是否出現方向性拒絕：
        bull → 下影線 >= 40% 總幅、且收盤在區間中點以上
        bear → 上影線 >= 40% 總幅、且收盤在區間中點以下
        """
        zone_mid = (zone['high'] + zone['low']) / 2
        for _, c in df.tail(3).iterrows():
            total = c['high'] - c['low']
            if total == 0:
                continue
            if direction == 'bull':
                lower_wick = min(c['open'], c['close']) - c['low']
                if lower_wick / total >= 0.40 and c['close'] > zone_mid:
                    return True
            else:
                upper_wick = c['high'] - max(c['open'], c['close'])
                if upper_wick / total >= 0.40 and c['close'] < zone_mid:
                    return True
        return False

    # ── 訂單塊偵測 ────────────────────────────────────────────────────────────
    def _order_blocks(self, df: pd.DataFrame) -> list:
        obs = []
        for i in range(1, len(df) - 1):
            body_curr = abs(df.iloc[i]['close'] - df.iloc[i]['open'])
            body_next = abs(df.iloc[i+1]['close'] - df.iloc[i+1]['open'])
            strong    = body_next > body_curr * 1.5
            if (df.iloc[i]['close'] < df.iloc[i]['open'] and
                df.iloc[i+1]['close'] > df.iloc[i+1]['open'] and strong):
                obs.append({'type':'bullish','high':df.iloc[i]['high'],'low':df.iloc[i]['low'],'i':i})
            elif (df.iloc[i]['close'] > df.iloc[i]['open'] and
                  df.iloc[i+1]['close'] < df.iloc[i+1]['open'] and strong):
                obs.append({'type':'bearish','high':df.iloc[i]['high'],'low':df.iloc[i]['low'],'i':i})
        return obs[-15:]

    # ── 失衡區偵測 ────────────────────────────────────────────────────────────
    def _fvg(self, df: pd.DataFrame) -> list:
        gaps = []
        for i in range(1, len(df) - 1):
            if df.iloc[i-1]['high'] < df.iloc[i+1]['low']:
                gaps.append({'type':'bullish','high':df.iloc[i+1]['low'],'low':df.iloc[i-1]['high'],'i':i})
            elif df.iloc[i-1]['low'] > df.iloc[i+1]['high']:
                gaps.append({'type':'bearish','high':df.iloc[i-1]['low'],'low':df.iloc[i+1]['high'],'i':i})
        return gaps[-15:]

    # ── 結構突破 BOS（15m 用於最終確認）──────────────────────────────────────
    def _bos(self, df: pd.DataFrame, direction: str) -> bool:
        s = self._swings(df, w=3)
        price = df.iloc[-1]['close']
        if direction == 'bullish':
            highs = s['sh'].dropna()
            return len(highs) >= 1 and price > highs.iloc[-1]
        else:
            lows = s['sl'].dropna()
            return len(lows) >= 1 and price < lows.iloc[-1]

    # ── 完整 SMC 進場邏輯 ─────────────────────────────────────────────────────
    def _check_direction(self, obs, fvgs, df_ref, df15m, price, label) -> Optional[Signal]:
        liq = self._liquidity_levels(df_ref)

        # ── 做多：掃低點流動性 → 位移上漲 → 回測看漲 OB/FVG → 拒絕 → 15m BOS ──
        swept_low = self._swept_liquidity(df_ref, liq['lows'], 'bull')
        if swept_low and self._has_displacement(df_ref, 'bull'):
            b_obs  = [o for o in obs  if o['type']=='bullish' and o['low'] <= price <= o['high']]
            b_fvgs = [f for f in fvgs if f['type']=='bullish' and f['low'] <= price <= f['high']]
            zone   = (b_obs or b_fvgs)[0] if (b_obs or b_fvgs) else None
            if zone and self._zone_rejection(df_ref, zone, 'bull') and self._bos(df15m, 'bullish'):
                sl  = swept_low * 0.9995
                highs_above = [h for h in liq['highs'] if h > price]
                tp  = min(highs_above) if highs_above else price * 1.008
                tag = 'OB+FVG' if b_obs and b_fvgs else ('OB' if b_obs else 'FVG')
                return Signal('LONG', price, sl, tp,
                              f'{label} 流動性掃除+位移+{tag}拒絕+15m BOS↑')

        # ── 做空：掃高點流動性 → 位移下跌 → 回測看跌 OB/FVG → 拒絕 → 15m BOS ──
        swept_high = self._swept_liquidity(df_ref, liq['highs'], 'bear')
        if swept_high and self._has_displacement(df_ref, 'bear'):
            s_obs  = [o for o in obs  if o['type']=='bearish' and o['low'] <= price <= o['high']]
            s_fvgs = [f for f in fvgs if f['type']=='bearish' and f['low'] <= price <= f['high']]
            zone   = (s_obs or s_fvgs)[0] if (s_obs or s_fvgs) else None
            if zone and self._zone_rejection(df_ref, zone, 'bear') and self._bos(df15m, 'bearish'):
                sl  = swept_high * 1.0005
                lows_below = [l for l in liq['lows'] if l < price]
                tp  = max(lows_below) if lows_below else price * 0.992
                tag = 'OB+FVG' if s_obs and s_fvgs else ('OB' if s_obs else 'FVG')
                return Signal('SHORT', price, sl, tp,
                              f'{label} 流動性掃除+位移+{tag}拒絕+15m BOS↓')

        return None

    # ── 主入口 ────────────────────────────────────────────────────────────────
    def get_signal(self) -> Optional[Signal]:
        df4h  = self._df(self.client.get_klines(self.symbol, '4h',  100))
        df1h  = self._df(self.client.get_klines(self.symbol, '1h',  100))
        df15m = self._df(self.client.get_klines(self.symbol, '15m', 60))
        price = df15m.iloc[-1]['close']

        # 優先用 4H 結構（訊號品質最高）
        signal = self._check_direction(
            self._order_blocks(df4h), self._fvg(df4h), df4h, df15m, price, '4H'
        )
        if signal:
            return signal

        # 無 4H 訊號再看 1H
        return self._check_direction(
            self._order_blocks(df1h), self._fvg(df1h), df1h, df15m, price, '1H'
        )
