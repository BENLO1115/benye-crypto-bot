import pandas as pd
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class Signal:
    direction:   str
    entry:       float
    stop_loss:   float
    take_profit: float
    reason:      str

class StrategyEngine:
    # 倫敦 08-10 UTC、晚盤 11-14 UTC（台灣 19-22）、紐約 13-16 UTC
    KILL_ZONES = [(8, 10), (11, 16)]

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

    # ── Kill Zone 時段過濾 ────────────────────────────────────────────────────
    def _in_kill_zone(self) -> bool:
        hour = datetime.utcnow().hour
        return any(s <= hour < e for s, e in self.KILL_ZONES)

    # ── 日線偏向（HTF Bias）──────────────────────────────────────────────────
    def _htf_bias(self, df_daily: pd.DataFrame) -> str:
        """
        日線連續更高高點+更高低點 = bullish
        連續更低高點+更低低點 = bearish
        否則 = neutral（多空都可做）
        """
        s = self._swings(df_daily, w=5)
        highs = s['sh'].dropna()
        lows  = s['sl'].dropna()
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs.iloc[-1] > highs.iloc[-2]
            hl = lows.iloc[-1]  > lows.iloc[-2]
            lh = highs.iloc[-1] < highs.iloc[-2]
            ll = lows.iloc[-1]  < lows.iloc[-2]
            if hh and hl:
                return 'bullish'
            if lh and ll:
                return 'bearish'
        return 'neutral'

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

    # ── 新鮮度檢查（未被回測過的區間）────────────────────────────────────────
    def _is_fresh(self, df: pd.DataFrame, zone: dict) -> bool:
        """
        區間形成後，後續K線（不含當前）若有進入區間範圍 → 已被回測 → 失效
        """
        post = df.iloc[zone['i'] + 2 : -1]
        for _, c in post.iterrows():
            if zone['type'] == 'bullish' and c['low'] <= zone['high'] and c['high'] >= zone['low']:
                return False
            if zone['type'] == 'bearish' and c['high'] >= zone['low'] and c['low'] <= zone['high']:
                return False
        return True

    # ── 流動性池 ──────────────────────────────────────────────────────────────
    def _liquidity_levels(self, df: pd.DataFrame) -> dict:
        s = self._swings(df, w=5)
        return {
            'highs': s['sh'].dropna().tail(8).tolist(),
            'lows':  s['sl'].dropna().tail(8).tolist(),
        }

    # ── 流動性掃除偵測 ────────────────────────────────────────────────────────
    def _swept_liquidity(self, df: pd.DataFrame, levels: list, direction: str) -> Optional[float]:
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

    # ── 區間拒絕確認 ──────────────────────────────────────────────────────────
    def _zone_rejection(self, df: pd.DataFrame, zone: dict, direction: str) -> bool:
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

    # ── BOS 確認 ──────────────────────────────────────────────────────────────
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
    def _check_direction(self, obs, fvgs, df_ref, df15m, price,
                         label, bias, min_rr) -> Optional[Signal]:
        liq = self._liquidity_levels(df_ref)

        # ── 做多（日線需為多頭或中性）──────────────────────────────────────
        if bias != 'bearish':
            swept_low = self._swept_liquidity(df_ref, liq['lows'], 'bull')
            if swept_low and self._has_displacement(df_ref, 'bull'):
                b_obs  = [o for o in obs  if o['type']=='bullish'
                          and o['low'] <= price <= o['high']
                          and self._is_fresh(df_ref, o)]
                b_fvgs = [f for f in fvgs if f['type']=='bullish'
                          and f['low'] <= price <= f['high']
                          and self._is_fresh(df_ref, f)]
                zone = (b_obs or b_fvgs)[0] if (b_obs or b_fvgs) else None
                if zone and self._zone_rejection(df_ref, zone, 'bull') and self._bos(df15m, 'bullish'):
                    sl  = swept_low * 0.9995
                    highs_above = [h for h in liq['highs'] if h > price]
                    tp  = min(highs_above) if highs_above else price * 1.008
                    rr  = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
                    if rr >= min_rr:
                        entry = (zone['high'] + zone['low']) / 2
                        tag   = 'OB+FVG' if b_obs and b_fvgs else ('OB' if b_obs else 'FVG')
                        return Signal('LONG', round(entry, 1), round(sl, 1), round(tp, 1),
                                      f'{label} [{bias}] 流動性掃除+{tag}拒絕+BOS↑ RR:{rr:.1f}')

        # ── 做空（日線需為空頭或中性）──────────────────────────────────────
        if bias != 'bullish':
            swept_high = self._swept_liquidity(df_ref, liq['highs'], 'bear')
            if swept_high and self._has_displacement(df_ref, 'bear'):
                s_obs  = [o for o in obs  if o['type']=='bearish'
                          and o['low'] <= price <= o['high']
                          and self._is_fresh(df_ref, o)]
                s_fvgs = [f for f in fvgs if f['type']=='bearish'
                          and f['low'] <= price <= f['high']
                          and self._is_fresh(df_ref, f)]
                zone = (s_obs or s_fvgs)[0] if (s_obs or s_fvgs) else None
                if zone and self._zone_rejection(df_ref, zone, 'bear') and self._bos(df15m, 'bearish'):
                    sl  = swept_high * 1.0005
                    lows_below = [l for l in liq['lows'] if l < price]
                    tp  = max(lows_below) if lows_below else price * 0.992
                    rr  = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
                    if rr >= min_rr:
                        entry = (zone['high'] + zone['low']) / 2
                        tag   = 'OB+FVG' if s_obs and s_fvgs else ('OB' if s_obs else 'FVG')
                        return Signal('SHORT', round(entry, 1), round(sl, 1), round(tp, 1),
                                      f'{label} [{bias}] 流動性掃除+{tag}拒絕+BOS↓ RR:{rr:.1f}')

        return None

    # ── 主入口 ────────────────────────────────────────────────────────────────
    def get_signal(self, min_rr: float = 1.5, check_kill_zone: bool = True) -> Optional[Signal]:
        if check_kill_zone and not self._in_kill_zone():
            return None

        df_daily = self._df(self.client.get_klines(self.symbol, '1d', 50))
        df4h     = self._df(self.client.get_klines(self.symbol, '4h', 100))
        df1h     = self._df(self.client.get_klines(self.symbol, '1h', 100))
        df15m    = self._df(self.client.get_klines(self.symbol, '15m', 60))
        price    = df15m.iloc[-1]['close']
        bias     = self._htf_bias(df_daily)

        signal = self._check_direction(
            self._order_blocks(df4h), self._fvg(df4h), df4h, df15m, price, '4H', bias, min_rr
        )
        if signal:
            return signal

        return self._check_direction(
            self._order_blocks(df1h), self._fvg(df1h), df1h, df15m, price, '1H', bias, min_rr
        )
