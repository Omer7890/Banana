################### All-in-one Server + Auto-loop + WebSocket + Dashboard + Charts ###################

# Install deps:
# pip install python-binance pandas pandas_ta python-dotenv requests tradingview-ta yfinance scikit-learn joblib matplotlib flask plotly websockets

import os, argparse
import pandas as pd
from flask import Flask, render_template_string, jsonify, request
from binance.client import Client

GLOBAL_CACHE = {
    'signals': [],
    'backtests': {},
    'last_update': None
}

app = Flask(__name__)

# Dashboard template with chart link
DASH_TEMPLATE = '''
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Crypto Signals Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
  <style>
    body{font-family: Arial, sans-serif; margin:20px}
    table{border-collapse: collapse; width:100%}
    th,td{border:1px solid #ddd; padding:8px}
    th{background:#f2f2f2}
    .confident{background:#d4edda}
    .buy{background:#cce5ff}
    .sell{background:#f8d7da}
    .hold{background:#fff3cd}
  </style>
</head>
<body>
  <h1>Top Signals (Last update: {{last_update}})</h1>
  <div id="table"></div>

<script>
async function fetchSignals(){
  const r = await fetch('/api/signals');
  const data = await r.json();
  const rows = data.map(r => `
    <tr class="${r.row_class}">
      <td>${r.symbol}</td>
      <td>${r.suggestion}</td>
      <td>${r.score.toFixed(2)}</td>
      <td>${r.notes}</td>
      <td><a href=\"/chart/${r.symbol}\" target=\"_blank\">View Chart</a></td>
      <td><a href=\"/backtest/${r.symbol}\">Backtest</a></td>
    </tr>`).join('');
  const html = `<table><tr><th>Symbol</th><th>Suggestion</th><th>Score</th><th>Notes</th><th>Chart</th><th>Backtest</th></tr>${rows}</table>`;
  document.getElementById('table').innerHTML = html;
}

fetchSignals();
setInterval(fetchSignals, 60000);
</script>
</body>
</html>
'''

# Chart template with timeframe selector + volume spikes
CHART_TEMPLATE = '''
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{{symbol}} Chart</title>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
</head>
<body>
  <h2>{{symbol}} Candlestick + Indicators</h2>

  <label for="interval">Timeframe:</label>
  <select id="interval">
    <option value="1m">1m</option>
    <option value="15m">15m</option>
    <option value="1h">1h</option>
    <option value="4h">4h</option>
  </select>

  <div id="chart" style="width:100%;height:900px"></div>

<script>
async function fetchData(){
  const interval = document.getElementById('interval').value;
  const r = await fetch(`/api/chartdata/{{symbol}}?interval=${interval}`);
  const d = await r.json();

  const candle = {
    x: d.time,
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
    type: 'candlestick',
    name: '{{symbol}}',
    xaxis: 'x',
    yaxis: 'y'
  };

  const ma20 = {x:d.time, y:d.ma20, type:'scatter', mode:'lines', name:'MA20'};
  const ma50 = {x:d.time, y:d.ma50, type:'scatter', mode:'lines', name:'MA50'};
  const upper = {x:d.time, y:d.bb_upper, type:'scatter', mode:'lines', name:'BB Upper'};
  const lower = {x:d.time, y:d.bb_lower, type:'scatter', mode:'lines', name:'BB Lower'};

  // Volume bars with color (green/red)
  const volumeColors = d.close.map((c,i) => c > d.open[i] ? 'green' : 'red');
  const volume = {x:d.time, y:d.volume, type:'bar', name:'Volume', marker:{color:volumeColors, opacity:0.4}, yaxis:'y'};
  const volma20 = {x:d.time, y:d.volma20, type:'scatter', mode:'lines', name:'VolMA20', line:{color:'blue', width:1}, yaxis:'y'};

  // RSI + MACD
  const rsi = {x:d.time, y:d.rsi, type:'scatter', mode:'lines', name:'RSI', yaxis:'y2'};
  const macd = {x:d.time, y:d.macd, type:'scatter', mode:'lines', name:'MACD', yaxis:'y3'};
  const macd_signal = {x:d.time, y:d.macd_signal, type:'scatter', mode:'lines', name:'MACD Signal', yaxis:'y3'};

  // Highlight volume spikes (yellow dots with label)
  const spikeX = [];
  const spikeY = [];
  const spikeText = [];
  d.volume.forEach((v,i) => {
    if(v > 2 * d.volma20[i]){
      spikeX.push(d.time[i]);
      spikeY.push(d.high[i]);
      spikeText.push('VOL SPIKE');
    }
  });

  const spikes = {
    x: spikeX,
    y: spikeY,
    text: spikeText,
    mode: 'markers+text',
    textposition: 'top center',
    marker: {color:'yellow', size:10, line:{color:'black', width:1}},
    name: 'Volume Spikes',
    yaxis:'y'
  };

  const layout = {
    grid: {rows:3, columns:1, pattern:'independent'},
    height:1000,
    title:`{{symbol}} Indicators (${interval})`,
    yaxis: {domain:[0.4,1]},
    yaxis2: {domain:[0.2,0.4]},
    yaxis3: {domain:[0,0.2]}
  };

  Plotly.newPlot('chart', [candle, ma20, ma50, upper, lower, volume, volma20, spikes, rsi, macd, macd_signal], layout);
}

document.getElementById('interval').addEventListener('change', fetchData);

fetchData();
</script>
</body>
</html>
'''

@app.route('/')
def dashboard_home():
    return render_template_string(DASH_TEMPLATE, last_update=GLOBAL_CACHE.get('last_update'))

@app.route('/api/signals')
def api_signals():
    out = []
    for r in GLOBAL_CACHE.get('signals', []):
        sug = r.get('final', {}).get('suggestion') if isinstance(r.get('final'), dict) else r.get('final')
        score = r.get('score', 0)
        notes = '; '.join(r.get('final', {}).get('notes', [])) if r.get('final') else ''
        cls = 'hold'
        if isinstance(sug, str):
            if 'CONFIDENT' in sug or 'STRONG' in sug:
                cls = 'confident'
            elif 'BUY' in sug:
                cls = 'buy'
            elif 'SELL' in sug:
                cls = 'sell'
        out.append({'symbol': r['symbol'], 'suggestion': sug, 'score': score, 'notes': notes, 'row_class': cls})
    return jsonify(out)

@app.route('/chart/<symbol>')
def chart_page(symbol):
    return render_template_string(CHART_TEMPLATE, symbol=symbol)

@app.route('/api/chartdata/<symbol>')
def chart_data(symbol):
    interval = request.args.get('interval', '1m')
    client = Client(os.getenv('BINANCE_KEY'), os.getenv('BINANCE_SECRET'))
    klines = client.get_klines(symbol=symbol, interval=interval, limit=200)
    df = pd.DataFrame(klines, columns=["t","o","h","l","c","v","ct","qav","n","tbbav","tbqav","i"])
    df['t'] = pd.to_datetime(df['t'], unit='ms')
    df = df.astype(float, errors='ignore')

    import pandas_ta as ta
    df['ma20'] = ta.sma(df['c'], length=20)
    df['ma50'] = ta.sma(df['c'], length=50)
    bb = ta.bbands(df['c'], length=20)
    df['bb_upper'] = bb['BBU_20_2.0']
    df['bb_lower'] = bb['BBL_20_2.0']
    df['rsi'] = ta.rsi(df['c'], length=14)
    macd = ta.macd(df['c'])
    df['macd'] = macd['MACD_12_26_9']
    df['macd_signal'] = macd['MACDs_12_26_9']
    df['volma20'] = ta.sma(df['v'], length=20)

    return jsonify({
        'time': df['t'].dt.strftime('%Y-%m-%d %H:%M').tolist(),
        'open': df['o'].tolist(),
        'high': df['h'].tolist(),
        'low': df['l'].tolist(),
        'close': df['c'].tolist(),
        'volume': df['v'].tolist(),
        'ma20': df['ma20'].tolist(),
        'ma50': df['ma50'].tolist(),
        'bb_upper': df['bb_upper'].tolist(),
        'bb_lower': df['bb_lower'].tolist(),
        'rsi': df['rsi'].tolist(),
        'macd': df['macd'].tolist(),
        'macd_signal': df['macd_signal'].tolist(),
        'volma20': df['volma20'].tolist()
    })

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Unified app with charts, timeframes, and volume spikes')
    parser.add_argument('--server', action='store_true')
    args = parser.parse_known_args()[0]

    if args.server:
        app.run(host='0.0.0.0', port=8080, debug=False)
    else:
        print('Run with --server to start dashboard')
