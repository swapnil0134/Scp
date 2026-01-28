import os
import json
import pandas as pd
import requests
from datetime import datetime, timedelta

# --- SETTINGS ---
TICKER = "QQQ"  # Nasdaq ETF Proxy
LEVERAGE = 20
INITIAL_CAPITAL = 1200.0
TP_PCT, SL_PCT = 0.0005, 0.0015
ATR_MIN, ATR_MAX = 8, 18
COOLDOWN_MINS = 30
BASE_DIR = "." # Current directory for GitHub persistence
STATE_FILE = os.path.join(BASE_DIR, "trading_state.json")
JOURNAL_FILE = os.path.join(BASE_DIR, "trading_journal.csv")
API_KEY = os.getenv("POLYGON_API_KEY")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"balance": INITIAL_CAPITAL, "active_trade": None, "last_exit_time": None}
    with open(STATE_FILE, 'r') as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def log_to_journal(trade_data):
    df = pd.DataFrame([trade_data])
    file_exists = os.path.exists(JOURNAL_FILE)
    df.to_csv(JOURNAL_FILE, mode='a', header=not file_exists, index=False)

def get_data():
    # Fetch 250 mins of data to calculate indicators accurately
    end = datetime.now()
    start = end - timedelta(minutes=250)
    url = f"https://api.polygon.io/v2/aggs/ticker/{TICKER}/range/1/minute/{int(start.timestamp()*1000)}/{int(end.timestamp()*1000)}?apiKey={API_KEY}"
    resp = requests.get(url).json()
    if "results" not in resp: return pd.DataFrame()
    
    df = pd.DataFrame(resp["results"])
    df.rename(columns={'o':'Open', 'h':'High', 'l':'Low', 'c':'Close', 't':'Timestamp'}, inplace=True)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
    df.set_index('Timestamp', inplace=True)
    return df

def run_cycle():
    try:
        state = load_state()
        df = get_data()
        if df.empty: return

        now_ts = df.index[-1]
        price = float(df['Close'].iloc[-1])
        high, low = float(df['High'].iloc[-1]), float(df['Low'].iloc[-1])

        # --- MONITOR ACTIVE TRADE ---
        if state['active_trade']:
            t = state['active_trade']
            hit_tp = (high >= t['tp'] if t['type'] == 'LONG' else low <= t['tp'])
            hit_sl = (low <= t['sl'] if t['type'] == 'LONG' else high >= t['sl'])
            
            if hit_tp or hit_sl:
                exit_p = t['tp'] if hit_tp else t['sl']
                pnl = (exit_p - t['entry_price']) * t['qty'] if t['type'] == 'LONG' else (t['entry_price'] - exit_p) * t['qty']
                state['balance'] += pnl
                log_to_journal({
                    "entry_time": t['entry_time'], "exit_time": now_ts.isoformat(),
                    "type": t['type'], "entry_price": round(t['entry_price'], 2),
                    "exit_price": round(exit_p, 2), "qty": round(t['qty'], 4),
                    "pnl": round(pnl, 2), "final_balance": round(state['balance'], 2)
                })
                state['active_trade'], state['last_exit_time'] = None, now_ts.isoformat()
                save_state(state)
            return

        # --- ENTRY LOGIC ---
        if state['last_exit_time']:
            last_exit = pd.to_datetime(state['last_exit_time']).replace(tzinfo=now_ts.tzinfo)
            if now_ts < last_exit + timedelta(minutes=COOLDOWN_MINS): return

        # Indicators
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        ema_val = df['Close'].resample('5min').last().ffill().ewm(span=200, adjust=False).mean().iloc[-1]
        atr = df['ATR'].iloc[-1]

        if ATR_MIN <= atr <= ATR_MAX:
            direction = "LONG" if price > ema_val and price > df['Open'].iloc[-1] else "SHORT" if price < ema_val and price < df['Open'].iloc[-1] else None
            if direction:
                state['active_trade'] = {
                    "type": direction, "entry_time": now_ts.isoformat(), "entry_price": price,
                    "qty": (state['balance'] * LEVERAGE) / price,
                    "tp": price * (1+TP_PCT) if direction == "LONG" else price * (1-TP_PCT),
                    "sl": price * (1-SL_PCT) if direction == "LONG" else price * (1+SL_PCT)
                }
                save_state(state)
                print(f"[{now_ts}] OPENED {direction} @ {price}")

    except Exception as e: print(f"ERROR: {str(e)}")

if __name__ == "__main__": run_cycle()
