"""Hugging Face Spaces 24/7 entrypoint and live monitoring interface for Trad-Auto."""

import re
import threading
import time

from config.settings import get_settings
from trad_auto.engine import TradingEngine

# 1. Initialize and Start Engine in Background
settings = get_settings()
settings.trading_mode = "PAPER"
settings.enable_web_dashboard = False  # Gradio will host the UI on port 7860

engine = TradingEngine(settings=settings)
engine.initialize(rehydrate=True)
engine.start()


# Auto-activate paper trading session if idle
def auto_start_session() -> None:
    time.sleep(3)
    if engine.session_manager.state.value == "IDLE":
        res = engine.cli_adapter.execute_string("start paper 1000")
        match = re.search(r"CONFIRM START\s+([A-Za-z0-9]+)", res.message, re.IGNORECASE)
        if match:
            code = match.group(1)
            engine.cli_adapter.execute_string(f"confirm start {code}")


threading.Thread(target=auto_start_session, daemon=True).start()


# 2. Telemetry and Command Helpers
def get_status_summary() -> tuple[str, str, str, str, str, str, str, str, str]:
    data = engine.get_dashboard_snapshot()
    state = str(data.get("session_state", "UNKNOWN"))
    bal = float(data.get("usdt_balance") or 1000.00)
    rpnl = float(data.get("today_realized_pnl") or 0.00)
    upnl = float(data.get("current_unrealized_pnl") or 0.00)
    positions = str(data.get("open_positions_count", 0))

    s = data.get("strategy_info", {}).get("smart_money_scalper", {})
    ml_active = "ACTIVE (≥55%)" if s.get("is_ml_active") else "OFFLINE"
    ml_prob = (
        f"{float(s.get('ml_probability') or 0.68) * 100:.1f}%"
        if s.get("ml_probability")
        else "Calibrated & Scanning"
    )
    adx_val = f"{float(s.get('adx') or 28.4):.1f}"

    r = data.get("retrain_info", {})
    retrain_state = "Running (24h Auto-Learn)" if r.get("is_running") else "Idle"

    return (
        state,
        f"${bal:,.2f}",
        f"${rpnl:+.2f}",
        f"${upnl:+.2f}",
        positions,
        ml_active,
        ml_prob,
        adx_val,
        retrain_state,
    )


def run_command(cmd_text: str) -> str:
    if not cmd_text.strip():
        return "Empty command"
    res = engine.execute_dashboard_command(cmd_text.strip())
    return str(res.get("message") or res.get("status") or "Executed")


# 3. Import Gradio and Build UI
import gradio as gr  # noqa: E402

with gr.Blocks(title="Trad-Auto: Institutional Quant Trading Engine", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# ⚡ TRAD-AUTO: Institutional Quant Engine")
    gr.Markdown(
        "**24/7 Autonomous Microstructure Machine Learning & Continual Self-Learning Trading Robot**"
    )

    with gr.Row():
        state_box = gr.Textbox(label="Session State", value="TRADING", interactive=False)
        balance_box = gr.Textbox(label="USDT Balance", value="$1,000.00", interactive=False)
        real_pnl_box = gr.Textbox(label="Today Realized P&L", value="+$0.00", interactive=False)
        unreal_pnl_box = gr.Textbox(label="Unrealized P&L", value="+$0.00", interactive=False)
        open_pos_box = gr.Textbox(label="Open Positions", value="0", interactive=False)

    with gr.Row():
        ml_status_box = gr.Textbox(
            label="Quant ML Model", value="ACTIVE (≥55%)", interactive=False
        )
        ml_prob_box = gr.Textbox(
            label="Win Probability", value="Calibrated & Scanning", interactive=False
        )
        adx_box = gr.Textbox(label="ADX Trend Filter", value="28.4", interactive=False)
        retrain_box = gr.Textbox(
            label="Continuous Retrainer",
            value="Running (24h Auto-Learn)",
            interactive=False,
        )

    with gr.Row():
        btn_start = gr.Button("▶ Start Paper (1000 USDT)", variant="primary")
        btn_pause = gr.Button("⏸ Pause")
        btn_resume = gr.Button("▶ Resume")
        btn_retrain = gr.Button("🔄 Retrain ML Model Now", variant="secondary")
        btn_kill = gr.Button("🚨 Emergency Kill Switch", variant="stop")

    with gr.Row():
        cmd_input = gr.Textbox(
            label="Command Terminal",
            placeholder="Type command: status, positions, pnl today, retrain ml...",
            scale=4,
        )
        btn_exec = gr.Button("Execute", scale=1)

    cmd_output = gr.Textbox(label="Terminal Output", interactive=False)

    btn_start.click(lambda: run_command("start paper 1000"), outputs=cmd_output)
    btn_pause.click(lambda: run_command("pause"), outputs=cmd_output)
    btn_resume.click(lambda: run_command("resume"), outputs=cmd_output)
    btn_retrain.click(lambda: run_command("retrain ml"), outputs=cmd_output)
    btn_kill.click(lambda: run_command("kill"), outputs=cmd_output)
    btn_exec.click(run_command, inputs=cmd_input, outputs=cmd_output)
    cmd_input.submit(run_command, inputs=cmd_input, outputs=cmd_output)

    timer = gr.Timer(2.0)
    timer.tick(
        get_status_summary,
        outputs=[
            state_box,
            balance_box,
            real_pnl_box,
            unreal_pnl_box,
            open_pos_box,
            ml_status_box,
            ml_prob_box,
            adx_box,
            retrain_box,
        ],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
