#!/usr/bin/env python3
"""
本爺機器人績效儀表板
Railway 部署：新增 Web Service，Start Command = python dashboard_server.py
"""
from flask import Flask, jsonify
from binance_client import BinanceClient
from config import Config
from datetime import datetime, date
import os

app    = Flask(__name__)
client = BinanceClient(Config.API_KEY, Config.SECRET_KEY)


@app.route('/')
def index():
    return DASHBOARD_HTML


@app.route('/api/data')
def api_data():
    try:
        balance   = client.get_balance()
        positions = client.get_positions(Config.SYMBOL)
        income    = client.get_income_history('REALIZED_PNL', limit=500)

        trades = [t for t in income if float(t.get('income', 0)) != 0]
        wins   = [t for t in trades if float(t['income']) > 0]
        losses = [t for t in trades if float(t['income']) < 0]
        total  = len(trades)

        win_rate      = len(wins) / total * 100 if total else 0
        gross_profit  = sum(float(t['income']) for t in wins)
        gross_loss    = abs(sum(float(t['income']) for t in losses)) or 0.001
        profit_factor = gross_profit / gross_loss

        today_str     = date.today().isoformat()
        today_trades  = [t for t in trades if
                         datetime.fromtimestamp(t['time'] / 1000).date().isoformat() == today_str]
        today_pnl     = sum(float(t['income']) for t in today_trades)

        # 資金曲線（按日累計）
        by_date: dict = {}
        for t in sorted(trades, key=lambda x: x['time']):
            d = datetime.fromtimestamp(t['time'] / 1000).date().isoformat()
            by_date[d] = by_date.get(d, 0) + float(t['income'])

        cum, equity = 0.0, []
        for d in sorted(by_date):
            cum += by_date[d]
            equity.append({'date': d, 'pnl': round(cum, 4)})

        # 最大回撤
        peak, max_dd = 0.0, 0.0
        for e in equity:
            if e['pnl'] > peak:
                peak = e['pnl']
            dd = (peak - e['pnl']) / max(abs(peak), 0.001) * 100
            max_dd = max(max_dd, dd)

        # 連敗
        consec = 0
        for t in reversed(trades[-20:]):
            if float(t['income']) < 0:
                consec += 1
            else:
                break

        # 當前倉位
        pos = None
        if positions:
            p   = positions[0]
            amt = float(p['positionAmt'])
            pos = {
                'direction':      'LONG' if amt > 0 else 'SHORT',
                'entry':          float(p['entryPrice']),
                'mark':           float(p['markPrice']),
                'size':           abs(amt),
                'unrealized_pnl': round(float(p.get('unRealizedProfit', 0)), 4),
            }

        # 最近 20 筆交易
        recent = []
        for t in sorted(trades, key=lambda x: x['time'], reverse=True)[:20]:
            pnl = float(t['income'])
            recent.append({
                'time': datetime.fromtimestamp(t['time'] / 1000).strftime('%m/%d %H:%M'),
                'pnl':  round(pnl, 4),
                'win':  pnl > 0,
            })

        return jsonify({
            'balance':        round(balance, 2),
            'today_pnl':      round(today_pnl, 4),
            'today_trades':   len(today_trades),
            'total_trades':   total,
            'win_rate':       round(win_rate, 1),
            'profit_factor':  round(profit_factor, 2),
            'max_drawdown':   round(max_dd, 1),
            'consec_loss':    consec,
            'position':       pos,
            'equity':         equity,
            'recent_trades':  recent,
            'last_updated':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 儀表板 HTML ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>本爺機器人 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#f0f2f5;color:#1a1d23;font-family:'Inter',system-ui,sans-serif;min-height:100vh}

  /* ── Header ── */
  .topbar{background:#fff;border-bottom:1px solid #e8eaed;padding:0 24px;height:60px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10;box-shadow:0 1px 4px rgba(0,0,0,.06)}
  .logo{display:flex;align-items:center;gap:10px}
  .logo-icon{width:32px;height:32px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:16px}
  .logo-text{font-size:1rem;font-weight:700;color:#1a1d23;letter-spacing:-.02em}
  .logo-sub{font-size:.7rem;color:#9ca3af;font-weight:400;display:block;line-height:1}
  .live-dot{width:8px;height:8px;border-radius:50%;background:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.2);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{box-shadow:0 0 0 3px rgba(16,185,129,.2)}50%{box-shadow:0 0 0 6px rgba(16,185,129,.05)}}
  .topbar-right{display:flex;align-items:center;gap:10px}
  .update-chip{background:#f3f4f6;border-radius:20px;padding:5px 12px;font-size:.72rem;color:#6b7280;font-weight:500}

  /* ── Layout ── */
  .page{max-width:900px;margin:0 auto;padding:20px 16px 40px}

  /* ── Stat Cards ── */
  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}
  .card{background:#fff;border-radius:14px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 8px rgba(0,0,0,.04);position:relative;overflow:hidden}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent,#6366f1);border-radius:14px 14px 0 0}
  .card-label{font-size:.72rem;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
  .card-value{font-size:1.55rem;font-weight:800;letter-spacing:-.03em;color:#1a1d23}
  .card-value.green{color:#10b981}
  .card-value.red{color:#ef4444}
  .card-value.blue{color:#6366f1}
  .card-value.yellow{color:#f59e0b}
  .card-icon{position:absolute;right:16px;top:50%;transform:translateY(-50%);font-size:1.6rem;opacity:.12}

  /* ── Position Card ── */
  .pos-card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 8px rgba(0,0,0,.04)}
  .section-title{font-size:.78rem;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px;display:flex;align-items:center;gap:8px}
  .badge{padding:4px 12px;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.04em}
  .badge-long{background:#d1fae5;color:#059669}
  .badge-short{background:#fee2e2;color:#dc2626}
  .pos-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  .pos-item label{font-size:.72rem;color:#9ca3af;font-weight:600;display:block;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
  .pos-item span{font-size:1.05rem;font-weight:700;color:#1a1d23}
  .no-pos{color:#d1d5db;font-size:.9rem;text-align:center;padding:16px 0;font-weight:500}

  /* ── Chart Cards ── */
  .chart-card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 8px rgba(0,0,0,.04)}
  .chart-wrap{position:relative;height:220px}

  /* ── Table ── */
  table{width:100%;border-collapse:collapse}
  th{font-size:.7rem;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;padding:8px 14px;text-align:left;border-bottom:2px solid #f3f4f6}
  td{padding:11px 14px;font-size:.85rem;border-bottom:1px solid #f9fafb;color:#374151}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#fafafa}
  .pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:.75rem;font-weight:600}
  .pill-win{background:#d1fae5;color:#059669}
  .pill-loss{background:#fee2e2;color:#dc2626}

  /* ── Divider row ── */
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}

  /* ── Responsive ── */
  @media(max-width:640px){
    .cards{grid-template-columns:repeat(2,1fr)}
    .pos-grid{grid-template-columns:repeat(2,1fr)}
    .row2{grid-template-columns:1fr}
    .topbar{padding:0 16px}
    .logo-text{font-size:.9rem}
  }
  @media(max-width:360px){
    .cards{grid-template-columns:1fr 1fr}
    .card-value{font-size:1.3rem}
  }
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">
    <div class="logo-icon">₿</div>
    <div>
      <span class="logo-text">本爺機器人</span>
      <span class="logo-sub">BTC/USDT · 125x · SMC</span>
    </div>
  </div>
  <div class="topbar-right">
    <div class="live-dot"></div>
    <span class="update-chip" id="update-time">載入中...</span>
  </div>
</div>

<div class="page">

  <div class="cards">
    <div class="card" style="--accent:#6366f1">
      <div class="card-label">帳戶餘額</div>
      <div class="card-value" id="balance">—</div>
      <span class="card-icon">💰</span>
    </div>
    <div class="card" style="--accent:#10b981">
      <div class="card-label">今日損益</div>
      <div class="card-value" id="today-pnl">—</div>
      <span class="card-icon">📈</span>
    </div>
    <div class="card" style="--accent:#6366f1">
      <div class="card-label">勝率</div>
      <div class="card-value blue" id="win-rate">—</div>
      <span class="card-icon">🎯</span>
    </div>
    <div class="card" style="--accent:#f59e0b">
      <div class="card-label">盈利因子</div>
      <div class="card-value" id="profit-factor">—</div>
      <span class="card-icon">⚡</span>
    </div>
    <div class="card" style="--accent:#ef4444">
      <div class="card-label">最大回撤</div>
      <div class="card-value" id="max-dd">—</div>
      <span class="card-icon">📉</span>
    </div>
    <div class="card" style="--accent:#8b5cf6">
      <div class="card-label">總交易數</div>
      <div class="card-value" id="total-trades">—</div>
      <span class="card-icon">📊</span>
    </div>
  </div>

  <div class="pos-card">
    <div class="section-title">
      當前倉位
      <span id="pos-badge"></span>
    </div>
    <div id="pos-content"><div class="no-pos">— 目前無持倉 —</div></div>
  </div>

  <div class="chart-card">
    <div class="section-title">資金曲線（累計損益 USDT）</div>
    <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
  </div>

  <div class="chart-card">
    <div class="section-title">最近 20 筆交易</div>
    <table>
      <thead><tr><th>時間</th><th>損益</th><th>結果</th></tr></thead>
      <tbody id="trades-tbody"></tbody>
    </table>
  </div>

</div>

<script>
let equityChart = null;

function fmt(n, dec=2){ return n>=0?'+'+n.toFixed(dec):n.toFixed(dec); }

async function refresh(){
  try{
    const r = await fetch('/api/data');
    const d = await r.json();
    if(d.error){ console.error(d.error); return; }

    document.getElementById('update-time').textContent = d.last_updated.slice(11,16) + ' 更新';
    document.getElementById('balance').textContent = '$' + d.balance.toFixed(2);

    const tpEl = document.getElementById('today-pnl');
    tpEl.textContent = fmt(d.today_pnl) + ' U';
    tpEl.className = 'card-value ' + (d.today_pnl >= 0 ? 'green' : 'red');

    const pfEl = document.getElementById('profit-factor');
    pfEl.textContent = d.profit_factor.toFixed(2);
    pfEl.className = 'card-value ' + (d.profit_factor >= 1.5 ? 'green' : d.profit_factor >= 1 ? 'yellow' : 'red');

    document.getElementById('win-rate').textContent = d.win_rate.toFixed(1) + '%';

    const ddEl = document.getElementById('max-dd');
    ddEl.textContent = d.max_drawdown.toFixed(1) + '%';
    ddEl.className = 'card-value ' + (d.max_drawdown > 20 ? 'red' : d.max_drawdown > 10 ? 'yellow' : 'green');

    document.getElementById('total-trades').textContent = d.total_trades + ' 筆';

    const posContent = document.getElementById('pos-content');
    const posBadge   = document.getElementById('pos-badge');
    if(d.position){
      const p = d.position;
      const isLong = p.direction === 'LONG';
      posBadge.innerHTML = `<span class="badge ${isLong?'badge-long':'badge-short'}">${p.direction}</span>`;
      const pnlColor = p.unrealized_pnl >= 0 ? '#059669' : '#dc2626';
      posContent.innerHTML = `<div class="pos-grid">
        <div class="pos-item"><label>進場價</label><span>$${p.entry.toLocaleString()}</span></div>
        <div class="pos-item"><label>標記價</label><span>$${p.mark.toLocaleString()}</span></div>
        <div class="pos-item"><label>未實現損益</label><span style="color:${pnlColor}">${fmt(p.unrealized_pnl)} USDT</span></div>
      </div>`;
    } else {
      posBadge.innerHTML = '';
      posContent.innerHTML = '<div class="no-pos">— 目前無持倉 —</div>';
    }

    const labels = d.equity.map(e=>e.date);
    const values = d.equity.map(e=>e.pnl);
    const isPos   = values.length === 0 || values[values.length-1] >= 0;
    const lineClr = isPos ? '#6366f1' : '#ef4444';
    const fillClr = isPos ? 'rgba(99,102,241,.08)' : 'rgba(239,68,68,.08)';

    if(equityChart){
      equityChart.data.labels = labels;
      equityChart.data.datasets[0].data   = values;
      equityChart.data.datasets[0].borderColor     = lineClr;
      equityChart.data.datasets[0].backgroundColor = fillClr;
      equityChart.update('none');
    } else {
      const ctx = document.getElementById('equityChart').getContext('2d');
      equityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets:[{ data:values, borderColor:lineClr, backgroundColor:fillClr, borderWidth:2.5, pointRadius:values.length>30?0:4, pointBackgroundColor:'#fff', pointBorderColor:lineClr, pointBorderWidth:2, fill:true, tension:0.4 }] },
        options:{
          responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:false}, tooltip:{ backgroundColor:'#fff', titleColor:'#374151', bodyColor:'#6b7280', borderColor:'#e5e7eb', borderWidth:1, padding:10, callbacks:{ label: c => ' ' + fmt(c.raw)+' USDT' }}},
          scales:{
            x:{ grid:{color:'#f3f4f6'}, ticks:{color:'#9ca3af', maxTicksLimit:8, font:{size:11}}},
            y:{ grid:{color:'#f3f4f6'}, ticks:{color:'#9ca3af', font:{size:11}, callback: v => fmt(v)+'U'}}
          }
        }
      });
    }

    const tbody = document.getElementById('trades-tbody');
    tbody.innerHTML = d.recent_trades.map(t => `
      <tr>
        <td style="color:#9ca3af;font-size:.8rem">${t.time}</td>
        <td style="color:${t.win?'#059669':'#dc2626'};font-weight:700">${fmt(t.pnl)} USDT</td>
        <td><span class="pill ${t.win?'pill-win':'pill-loss'}">${t.win?'獲利':'虧損'}</span></td>
      </tr>`).join('');

  } catch(e){ console.error(e); }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f'Dashboard running on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
