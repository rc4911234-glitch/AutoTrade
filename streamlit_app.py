"""Streamlit Community Cloud 24/7 entrypoint for Trad-Auto.

Deployed at https://share.streamlit.io from GitHub repo rc4911234-glitch/AutoTrade.
The trading engine runs in a background daemon thread on CPU.
UptimeRobot pings every 5 min to prevent Streamlit Cloud hibernation.
"""

import re
import threading
import time
from typing import Any

import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Trad-Auto: Institutional Quant Engine",
    page_icon="⚡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Engine singleton — lives in st.session_state across reruns,
# but we also keep a module-level ref so the daemon thread survives.
# ---------------------------------------------------------------------------
_engine_lock = threading.Lock()


@st.cache_resource(show_spinner="🚀 Booting Trad-Auto Engine…")
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


# Boot engine (cached — only runs once per container lifecycle)
engine = get_engine()


# ---------------------------------------------------------------------------
# Helper: get dashboard snapshot
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
# UI
# ---------------------------------------------------------------------------
st.markdown("# ⚡ TRAD-AUTO: Institutional Quant Engine")
st.markdown(
    "**24/7 Autonomous Microstructure ML & Continual Self-Learning Trading Robot**"
)

data = get_snapshot()

# Row 1: Core metrics
col1, col2, col3, col4, col5 = st.columns(5)

state = str(data.get("session_state", "BOOTING…"))
bal = float(data.get("usdt_balance") or 1000.00)
rpnl = float(data.get("today_realized_pnl") or 0.00)
upnl = float(data.get("current_unrealized_pnl") or 0.00)
positions = int(data.get("open_positions_count", 0))

col1.metric("Session State", state)
col2.metric("USDT Balance", f"${bal:,.2f}")
col3.metric("Today Realized P&L", f"${rpnl:+.2f}", delta=f"{rpnl:+.2f}")
col4.metric("Unrealized P&L", f"${upnl:+.2f}", delta=f"{upnl:+.2f}")
col5.metric("Open Positions", positions)

# Row 2: ML & Strategy metrics
st.divider()
s = data.get("strategy_info", {}).get("smart_money_scalper", {})
r = data.get("retrain_info", {})

col6, col7, col8, col9 = st.columns(4)

ml_active = s.get("is_ml_active", False)
ml_prob = float(s.get("ml_probability") or 0) * 100 if s.get("ml_probability") else 0
adx_val = float(s.get("adx") or 0)
retrain_running = r.get("is_running", False)

col6.metric("Quant ML Model", "✅ ACTIVE (≥55%)" if ml_active else "⏸ OFFLINE")
col7.metric("Win Probability", f"{ml_prob:.1f}%" if ml_prob > 0 else "Scanning…")
col8.metric("ADX Trend Filter", f"{adx_val:.1f}" if adx_val > 0 else "—")
col9.metric(
    "Continuous Retrainer",
    "🔄 Running (24h)" if retrain_running else "⏸ Idle",
)

# Row 3: Action buttons
st.divider()
bcol1, bcol2, bcol3, bcol4, bcol5 = st.columns(5)

if bcol1.button("▶ Start Paper (1000 USDT)", type="primary", use_container_width=True):
    st.info(run_cmd("start paper 1000"))

if bcol2.button("⏸ Pause", use_container_width=True):
    st.warning(run_cmd("pause"))

if bcol3.button("▶ Resume", use_container_width=True):
    st.success(run_cmd("resume"))

if bcol4.button("🔄 Retrain ML Now", use_container_width=True):
    st.info(run_cmd("retrain ml"))

if bcol5.button("🚨 KILL", type="secondary", use_container_width=True):
    st.error(run_cmd("kill"))

# Row 4: Command terminal
st.divider()
st.markdown("### 💻 Command Terminal")
cmd = st.text_input(
    "Enter command",
    placeholder="status, positions, pnl today, retrain ml…",
    label_visibility="collapsed",
)
if cmd:
    output = run_cmd(cmd)
    st.code(output, language="text")

# Auto-refresh every 5 seconds
time.sleep(5)
st.rerun()
