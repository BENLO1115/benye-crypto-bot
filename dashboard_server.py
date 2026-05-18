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
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;padding:20px}
  h1{font-size:1.4rem;font-weight:600;color:#e6edf3}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #21262d}
  .last-update{font-size:.8rem;color:#8b949e}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
  .card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:18px}
  .card-label{font-size:.75rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
  .card-value{font-size:1.6rem;font-weight:700}
  .green{color:#3fb950}.red{color:#f85149}.blue{color:#388bfd}.yellow{color:#d29922}
  .pos-card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:18px;margin-bottom:20px}
  .pos-header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
  .badge{padding:3px 10px;border-radius:20px;font-size:.75rem;font-weight:600}
  .badge-long{background:#1a3a2a;color:#3fb950}.badge-short{background:#3a1a1a;color:#f85149}
  .pos-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .pos-item label{font-size:.7rem;color:#8b949e;display:block;margin-bottom:4px}
  .pos-item span{font-size:1rem;font-weight:600}
  .no-pos{color:#8b949e;font-size:.9rem;padding:8px 0}
  .chart-card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:20px;margin-bottom:20px}
  .chart-title{font-size:.85rem;color:#8b949e;margin-bottom:16px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
  .chart-wrap{position:relative;height:220px}
  table{width:100%;border-collapse:collapse}
  th{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;padding:8px 12px;text-align:left;border-bottom:1px solid #21262d}
  td{padding:10px 12px;font-size:.85rem;border-bottom:1px solid #161b22}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#1c2128}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
  .dot-green{background:#3fb950}.dot-red{background:#f85149}
  @media(max-width:600px){.pos-grid{grid-template-columns:repeat(2,1fr)}.cards{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="header">
  <h1>本爺機器人 Dashboard</h1>
  <span class="last-update" id="update-time">載入中...</span>
</div>

<div class="cards" id="stats-cards">
  <div class="card"><div class="card-label">帳戶餘額</div><div class="card-value" id="balance">—</div></div>
  <div class="card"><div class="card-label">今日損益</div><div class="card-value" id="today-pnl">—</div></div>
  <div class="card"><div class="card-label">勝率</div><div class="card-value blue" id="win-rate">—</div></div>
  <div class="card"><div class="card-label">盈利因子</div><div class="card-value" id="profit-factor">—</div></div>
  <div class="card"><div class="card-label">最大回撤</div><div class="card-value" id="max-dd">—</div></div>
  <div class="card"><div class="card-label">總交易數</div><div class="card-value" id="total-trades">—</div></div>
</div>

<div class="pos-card">
  <div class="pos-header"><span style="font-size:.85rem;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.05em">當前倉位</span><span id="pos-badge"></span></div>
  <div id="pos-content"><div class="no-pos">無持倉</div></div>
</div>

<div class="chart-card">
  <div class="chart-title">資金曲線（累計損益 USDT）</div>
  <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
</div>

<div class="chart-card">
  <div class="chart-title">最近 20 筆交易</div>
  <table>
    <thead><tr><th>時間</th><th>損益</th><th>結果</th></tr></thead>
    <tbody id="trades-tbody"></tbody>
  </table>
</div>

<script>
let equityChart = null;

function fmt(n, dec=2){ return n>=0?'+'+n.toFixed(dec):n.toFixed(dec); }

async function refresh(){
  try{
    const r = await fetch('/api/data');
    const d = await r.json();
    if(d.error){ console.error(d.error); return; }

    document.getElementById('update-time').textContent = '最後更新 ' + d.last_updated;
    document.getElementById('balance').textContent = '$' + d.balance.toFixed(2);

    const tpEl = document.getElementById('today-pnl');
    tpEl.textContent = fmt(d.today_pnl) + ' USDT';
    tpEl.className = 'card-value ' + (d.today_pnl >= 0 ? 'green' : 'red');

    const pfEl = document.getElementById('profit-factor');
    pfEl.textContent = d.profit_factor.toFixed(2);
    pfEl.className = 'card-value ' + (d.profit_factor >= 1.5 ? 'green' : d.profit_factor >= 1 ? 'yellow' : 'red');

    document.getElementById('win-rate').textContent = d.win_rate.toFixed(1) + '%';

    const ddEl = document.getElementById('max-dd');
    ddEl.textContent = d.max_drawdown.toFixed(1) + '%';
    ddEl.className = 'card-value ' + (d.max_drawdown > 20 ? 'red' : d.max_drawdown > 10 ? 'yellow' : 'green');

    document.getElementById('total-trades').textContent = d.total_trades + ' 筆';

    // 倉位
    const posContent = document.getElementById('pos-content');
    const posBadge   = document.getElementById('pos-badge');
    if(d.position){
      const p = d.position;
      const isLong = p.direction === 'LONG';
      posBadge.innerHTML = `<span class="badge ${isLong?'badge-long':'badge-short'}">${p.direction}</span>`;
      const pnlColor = p.unrealized_pnl >= 0 ? '#3fb950' : '#f85149';
      posContent.innerHTML = `<div class="pos-grid">
        <div class="pos-item"><label>進場價</label><span>$${p.entry.toLocaleString()}</span></div>
        <div class="pos-item"><label>標記價</label><span>$${p.mark.toLocaleString()}</span></div>
        <div class="pos-item"><label>未實現損益</label><span style="color:${pnlColor}">${fmt(p.unrealized_pnl)} USDT</span></div>
      </div>`;
    } else {
      posBadge.innerHTML = '';
      posContent.innerHTML = '<div class="no-pos">無持倉</div>';
    }

    // 資金曲線
    const labels = d.equity.map(e=>e.date);
    const values = d.equity.map(e=>e.pnl);
    const isPositive = values.length === 0 || values[values.length-1] >= 0;
    const lineColor  = isPositive ? '#3fb950' : '#f85149';

    if(equityChart){
      equityChart.data.labels = labels;
      equityChart.data.datasets[0].data = values;
      equityChart.data.datasets[0].borderColor = lineColor;
      equityChart.update('none');
    } else {
      const ctx = document.getElementById('equityChart').getContext('2d');
      equityChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets:[{
            data: values,
            borderColor: lineColor,
            backgroundColor: isPositive ? 'rgba(63,185,80,.08)' : 'rgba(248,81,73,.08)',
            borderWidth: 2,
            pointRadius: values.length > 30 ? 0 : 3,
            fill: true,
            tension: 0.3,
          }]
        },
        options:{
          responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label: c => fmt(c.raw)+' USDT' }}},
          scales:{
            x:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e', maxTicksLimit:8, font:{size:11}}},
            y:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e', font:{size:11}, callback: v => fmt(v)+' U'}}
          }
        }
      });
    }

    // 最近交易
    const tbody = document.getElementById('trades-tbody');
    tbody.innerHTML = d.recent_trades.map(t => `
      <tr>
        <td style="color:#8b949e">${t.time}</td>
        <td style="color:${t.win?'#3fb950':'#f85149'};font-weight:600">${fmt(t.pnl)} USDT</td>
        <td><span class="dot ${t.win?'dot-green':'dot-red'}"></span>${t.win?'獲利':'虧損'}</td>
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
