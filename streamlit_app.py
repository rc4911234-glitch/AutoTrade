"""Streamlit Community Cloud 24/7 entrypoint and institutional market terminal for Trad-Auto.

Deployed at https://share.streamlit.io from GitHub repo rc4911234-glitch/AutoTrade.
Features:
- Real-time Broad Market Telemetry (BTC, ETH, SOL, BNB, XRP, DOGE)
- Live Macro Indicators (Fear & Greed, Binance Futures Funding Rates)
- Institutional Risk & Machine Learning Conviction Gate
- Continuous Live Microstructure Candles & Trend Chart
- Fail-Closed Autonomous Execution Engine in Background Thread
"""

import re
import threading
import time
from typing import Any

import pandas as pd
import streamlit as st

from trad_auto.market_data.market_overview import get_market_overview

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Trad-Auto: Institutional Quant & Market Terminal",
    page_icon="⚡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Engine singleton — cached across all user sessions and reruns
# ---------------------------------------------------------------------------
_engine_lock = threading.Lock()


@st.cache_resource(show_spinner="🚀 Booting Trad-Auto Quant Engine…")
def get_engine() -> Any:
    """Initialize TradingEngine once (cached across all users/reruns)."""
    try:
        from config.settings import get_settings
        from trad_auto.engine import TradingEngine

        settings = get_settings()
        settings.trading_mode = "PAPER"
        settings.enable_web_dashboard = False  # Streamlit hosts UI

        eng = TradingEngine(settings=settings)
        eng.initialize(rehydrate=True)
        eng.start()

        # Auto-start paper session
        def _auto_start() -> None:
            time.sleep(3)
            try:
                if eng.session_manager.state.value == "IDLE":
                    res = eng.cli_adapter.execute_string("start paper 1000")
                    match = re.search(
                        r"CONFIRM START\s+([A-Za-z0-9]+)",
                        res.message,
                        re.IGNORECASE,
                    )
                    if match:
                        code = match.group(1)
                        eng.cli_adapter.execute_string(f"confirm start {code}")
            except Exception:
                pass

        threading.Thread(target=_auto_start, daemon=True).start()
        return eng
    except Exception as exc:
        st.error(f"Engine boot failed: {exc}")
        return None


engine = get_engine()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_snapshot() -> dict[str, Any]:
    if engine is None:
        return {}
    try:
        return engine.get_dashboard_snapshot()
    except Exception:
        return {}


def run_cmd(cmd: str) -> str:
    if engine is None:
        return "⏳ Engine is still booting…"
    if not cmd.strip():
        return "Empty command"
    try:
        res = engine.execute_dashboard_command(cmd.strip())
        return str(res.get("message") or res.get("status") or "Executed")
    except Exception as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Telemetry Data Ingestion
# ---------------------------------------------------------------------------
data = get_snapshot()
market = get_market_overview()
tickers = market.get("tickers", {})
funding = market.get("funding_rates", {})
fng = market.get("fear_greed", {})

# ---------------------------------------------------------------------------
# UI Header
# ---------------------------------------------------------------------------
st.markdown("# ⚡ TRAD-AUTO: Institutional Market & Quant Terminal")
st.caption(
    "**24/7 Autonomous Microstructure Machine Learning • Broad Market Intelligence • Aladdin-Grade Risk Protection**"
)

# ---------------------------------------------------------------------------
# SECTION 1: GLOBAL MARKET PULSE & SENTIMENT
# ---------------------------------------------------------------------------
st.markdown("### 🌐 Global Crypto Market Pulse")
mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)

fng_val = fng.get("value", "—")
fng_class = fng.get("classification", "Neutral")
mcol1.metric("Fear & Greed Index", f"{fng_val} ({fng_class})")

btc_funding = funding.get("BTCUSDT", 0.0)
mcol2.metric("BTC 8h Funding Rate", f"{btc_funding:+.4f}%")

eth_funding = funding.get("ETHUSDT", 0.0)
mcol3.metric("ETH 8h Funding Rate", f"{eth_funding:+.4f}%")

sol_funding = funding.get("SOLUSDT", 0.0)
mcol4.metric("SOL 8h Funding Rate", f"{sol_funding:+.4f}%")

news_info = data.get("news_info", {})
is_blackout = news_info.get("is_blackout", False)
mcol5.metric(
    "News Volatility Shield",
    "🛡️ BLACKOUT (Guarded)" if is_blackout else "🟢 CLEAR (Normal)",
)

if is_blackout:
    st.warning(
        "🛡️ **NEWS VOLATILITY SHIELD ACTIVE**: High-impact breaking crypto news detected in market feeds. "
        "New trade entries are temporarily blacked out to protect capital from sudden volatility wicks."
    )

# ---------------------------------------------------------------------------
# SECTION 2: BROAD MARKET TICKERS (LIVE 24H PRICING & VOLUME)
# ---------------------------------------------------------------------------
st.markdown("### 📊 Live Multi-Asset Market Overview (Binance Futures)")
tcol1, tcol2, tcol3, tcol4, tcol5, tcol6 = st.columns(6)

pairs = [
    ("BTCUSDT", tcol1, "Bitcoin"),
    ("ETHUSDT", tcol2, "Ethereum"),
    ("SOLUSDT", tcol3, "Solana"),
    ("BNBUSDT", tcol4, "BNB"),
    ("XRPUSDT", tcol5, "XRP"),
    ("DOGEUSDT", tcol6, "Dogecoin"),
]

for sym, col, name in pairs:
    t = tickers.get(sym, {})
    price = t.get("price", 0.0)
    chg = t.get("change", 0.0)
    vol_m = t.get("volume_m", 0.0)
    col.metric(
        label=f"{name} ({sym})",
        value=f"${price:,.2f}" if price >= 1.0 else f"${price:,.4f}",
        delta=f"{chg:+.2f}% (24h)",
    )

# ---------------------------------------------------------------------------
# SECTION 3: TRAD-AUTO QUANT BOT SESSION & CAPITAL
# ---------------------------------------------------------------------------
st.divider()
st.markdown("### 🤖 Trad-Auto Bot Portfolio & Performance")
pcol1, pcol2, pcol3, pcol4, pcol5 = st.columns(5)

state = str(data.get("session_state", "BOOTING…"))
bal = float(data.get("usdt_balance") or 1000.00)
rpnl = float(data.get("today_realized_pnl") or 0.00)
upnl = float(data.get("current_unrealized_pnl") or 0.00)
positions = int(data.get("open_positions_count", 0))

pcol1.metric("Session State", state)
pcol2.metric("USDT Balance", f"${bal:,.2f}")
pcol3.metric("Today Realized P&L", f"${rpnl:+.2f}", delta=f"{rpnl:+.2f}")
pcol4.metric("Unrealized P&L", f"${upnl:+.2f}", delta=f"{upnl:+.2f}")
pcol5.metric("Open Positions", positions)

# ---------------------------------------------------------------------------
# SECTION 4: 22-FACTOR QUANT ML & INDICATOR RIBBON
# ---------------------------------------------------------------------------
st.markdown("### 🔬 Strategy Conviction & Quantitative Indicators")
qcol1, qcol2, qcol3, qcol4, qcol5 = st.columns(5)

s = data.get("strategy_info", {}).get("smart_money_scalper", {})
r = data.get("retrain_info", {})

ml_active = s.get("is_ml_active", False)
ml_prob = float(s.get("ml_probability") or 0) * 100 if s.get("ml_probability") else 0

adx_raw = s.get("adx")
try:
    adx_val = float(adx_raw) if adx_raw is not None and str(adx_raw).strip() not in ("", "None") else 0.0
except Exception:
    adx_val = 0.0

rsi_raw = s.get("rsi")
try:
    rsi_val = float(rsi_raw) if rsi_raw is not None and str(rsi_raw).strip() not in ("", "None") else 0.0
except Exception:
    rsi_val = 0.0

trend_ema_raw = s.get("trend_ema")
trend_ema_str = f"${float(trend_ema_raw):,.1f}" if trend_ema_raw and str(trend_ema_raw).strip() not in ("", "None") else "—"

retrain_running = r.get("is_running", False)

qcol1.metric("Quant ML Model", "✅ ACTIVE (≥55%)" if ml_active else "⏸ OFFLINE")
qcol2.metric("Win Probability", f"{ml_prob:.1f}%" if ml_prob > 0 else "Scanning…")
qcol3.metric(
    "ADX Trend Filter",
    f"{adx_val:.1f} (" + ("Trending 🔥" if adx_val >= 22 else "Choppy ⏸") + ")" if adx_val > 0 else "Warming up…",
)
qcol4.metric("RSI (14)", f"{rsi_val:.1f}" if rsi_val > 0 else "—")
qcol5.metric(
    "Continuous Retrainer",
    "🔄 Running (24h)" if retrain_running else "⏸ Idle",
)

# ---------------------------------------------------------------------------
# SECTION 5: LIVE BTCUSDT RECENT CANDLE TREND
# ---------------------------------------------------------------------------
if engine is not None and hasattr(engine, "bar_store"):
    bars = engine.bar_store.get_bars("BTCUSDT", "1m", limit=40)
    if bars:
        st.markdown("### 📈 Live BTCUSDT 1-Minute Micro-Trend (Last 40 Candles)")
        chart_data = pd.DataFrame(
            {
                "Time": [b.timestamp.strftime("%H:%M") for b in bars],
                "Price": [float(b.close) for b in bars],
            }
        ).set_index("Time")
        st.line_chart(chart_data, height=220)

# ---------------------------------------------------------------------------
# SECTION 6: CONTROL ACTIONS & COMMAND TERMINAL
# ---------------------------------------------------------------------------
st.divider()
st.markdown("### 🎮 Control Center & Terminal")
bcol1, bcol2, bcol3, bcol4, bcol5 = st.columns(5)

if bcol1.button("▶ Start Paper (1000 USDT)", type="primary", use_container_width=True):
    st.info(run_cmd("start paper 1000"))

if bcol2.button("⏸ Pause", use_container_width=True):
    st.warning(run_cmd("pause"))

if bcol3.button("▶ Resume", use_container_width=True):
    st.success(run_cmd("resume"))

if bcol4.button("🔄 Retrain ML Now", use_container_width=True):
    st.info(run_cmd("retrain ml"))

if bcol5.button("🚨 KILL SWITCH", type="secondary", use_container_width=True):
    st.error(run_cmd("kill"))

cmd = st.text_input(
    "Command Terminal (type: status, positions, pnl today, retrain ml...)",
    placeholder="Type command and press Enter...",
)
if cmd:
    output = run_cmd(cmd)
    st.code(output, language="text")

# ---------------------------------------------------------------------------
# Auto-refresh cycle (every 5 seconds)
# ---------------------------------------------------------------------------
time.sleep(5)
st.rerun()
