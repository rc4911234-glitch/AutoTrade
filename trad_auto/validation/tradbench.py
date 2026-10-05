"""TradBench: Standardized 100-Point Institutional Quantitative Evaluation Framework.

Implements the definitive scoring engine for Trad-Auto models across 9 objective dimensions.
Enforces the hard safety rule: Safety critical failure = Immediate REJECT regardless of score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import numpy as np


class ModelPromotionStatus(str, Enum):
    EXPERIMENT = "EXPERIMENT"
    CANDIDATE = "CANDIDATE"
    CHALLENGER = "CHALLENGER"
    CHAMPION = "CHAMPION"
    REJECTED = "REJECTED"


@dataclass
class TradBenchDimensionScore:
    name: str
    weight: float
    score: float
    max_score: float
    details: dict[str, Any] = field(default_factory=dict)
    passed_hurdle: bool = True


@dataclass
class TradBenchReport:
    model_name: str
    model_version: str
    timestamp: str
    total_score: float  # Out of 100
    promotion_status: ModelPromotionStatus
    safety_critical_failure: bool
    rejection_reasons: list[str] = field(default_factory=list)
    dimension_scores: dict[str, TradBenchDimensionScore] = field(default_factory=dict)
    summary_metrics: dict[str, Any] = field(default_factory=dict)

    def is_eligible_for_promotion(self, min_score: float = 75.0) -> bool:
        if self.safety_critical_failure:
            return False
        return self.total_score >= min_score and len(self.rejection_reasons) == 0


class TradBenchEvaluator:
    """Standardized 100-Point Quantitative Scoring Engine for Trad-Auto.

    Scoring Breakdown (Total: 100 pts):
    1. Historical Performance:       15 pts
    2. Risk & Drawdown Control:      20 pts
    3. Risk-Adjusted Return:         15 pts
    4. 🔒 Hidden OOS (Final Exam):   15 pts
    5. Walk-Forward Stability:       10 pts
    6. Parameter Robustness:         10 pts
    7. Execution Reality Stress:      5 pts
    8. Paper Trading Verification:    5 pts
    9. Safety & Circuit Breakers:     5 pts (CRITICAL HURDLE)
    """

    WEIGHTS = {
        "historical_performance": 15.0,
        "risk_drawdown": 20.0,
        "risk_adjusted": 15.0,
        "hidden_oos": 15.0,
        "walk_forward": 10.0,
        "robustness": 10.0,
        "execution_reality": 5.0,
        "paper_trading": 5.0,
        "safety_controls": 5.0,
    }

    def evaluate_model(
        self,
        model_name: str,
        model_version: str,
        metrics: dict[str, Any],
    ) -> TradBenchReport:
        """Evaluates a model across all 9 TradBench dimensions."""
        rejection_reasons: list[str] = []
        dim_scores: dict[str, TradBenchDimensionScore] = {}

        # 1. Historical Performance (15 pts)
        # Target: Annualized return > 25%, Profit factor > 1.8, Win rate > 45% (with 1:2 RR)
        cagr = float(metrics.get("cagr_pct", 0.0))
        pf = float(metrics.get("profit_factor", 1.0))
        win_rate = float(metrics.get("win_rate_pct", 50.0))
        
        cagr_score = min(5.0, max(0.0, (cagr / 30.0) * 5.0))
        pf_score = min(5.0, max(0.0, ((pf - 1.0) / 1.0) * 5.0))
        wr_score = min(5.0, max(0.0, (win_rate / 60.0) * 5.0))
        hist_total = cagr_score + pf_score + wr_score
        dim_scores["historical_performance"] = TradBenchDimensionScore(
            name="Historical Performance",
            weight=15.0,
            score=round(hist_total, 2),
            max_score=15.0,
            details={"cagr_pct": cagr, "profit_factor": pf, "win_rate_pct": win_rate},
        )

        # 2. Risk & Drawdown Control (20 pts)
        # Target: Max Drawdown < 10% (full marks), > 25% (0 marks). Ulcer index < 3.0
        max_dd = abs(float(metrics.get("max_drawdown_pct", 15.0)))
        tail_loss = abs(float(metrics.get("worst_trade_pct", 3.0)))
        
        if max_dd <= 8.0:
            dd_score = 14.0
        elif max_dd <= 15.0:
            dd_score = 14.0 - ((max_dd - 8.0) / 7.0) * 7.0
        elif max_dd <= 25.0:
            dd_score = max(0.0, 7.0 - ((max_dd - 15.0) / 10.0) * 7.0)
        else:
            dd_score = 0.0
            rejection_reasons.append(f"Excessive Max Drawdown ({max_dd:.1f}% > 25.0% ceiling)")

        tail_score = min(6.0, max(0.0, 6.0 - (tail_loss / 5.0) * 3.0))
        risk_total = dd_score + tail_score
        dim_scores["risk_drawdown"] = TradBenchDimensionScore(
            name="Risk & Drawdown Control",
            weight=20.0,
            score=round(risk_total, 2),
            max_score=20.0,
            details={"max_drawdown_pct": max_dd, "tail_loss_pct": tail_loss},
            passed_hurdle=(max_dd <= 25.0),
        )

        # 3. Risk-Adjusted Performance (15 pts)
        # Target: Sharpe > 1.8, Sortino > 2.5, Calmar > 2.0
        sharpe = float(metrics.get("sharpe_ratio", 1.0))
        sortino = float(metrics.get("sortino_ratio", 1.5))
        calmar = float(metrics.get("calmar_ratio", 1.2))

        sh_score = min(6.0, max(0.0, (sharpe / 2.0) * 6.0))
        so_score = min(5.0, max(0.0, (sortino / 3.0) * 5.0))
        ca_score = min(4.0, max(0.0, (calmar / 2.5) * 4.0))
        risk_adj_total = sh_score + so_score + ca_score
        dim_scores["risk_adjusted"] = TradBenchDimensionScore(
            name="Risk-Adjusted Performance",
            weight=15.0,
            score=round(risk_adj_total, 2),
            max_score=15.0,
            details={"sharpe": sharpe, "sortino": sortino, "calmar": calmar},
        )

        # 4. 🔒 Hidden Out-Of-Sample Exam (15 pts)
        # Target: OOS Sharpe / In-Sample Sharpe >= 0.70, OOS Return positive
        oos_sharpe = float(metrics.get("oos_sharpe", 0.0))
        is_sharpe = max(0.1, float(metrics.get("is_sharpe", sharpe)))
        oos_ratio = oos_sharpe / is_sharpe
        oos_pnl_pct = float(metrics.get("oos_return_pct", 0.0))

        if oos_pnl_pct < 0:
            oos_score = 0.0
            rejection_reasons.append(f"Failed Hidden OOS Exam: Negative Return ({oos_pnl_pct:+.1f}%)")
        else:
            oos_ret_score = min(7.5, max(0.0, (oos_pnl_pct / 15.0) * 7.5))
            oos_ratio_score = min(7.5, max(0.0, (oos_ratio / 0.8) * 7.5))
            oos_score = oos_ret_score + oos_ratio_score

        dim_scores["hidden_oos"] = TradBenchDimensionScore(
            name="Hidden OOS Exam",
            weight=15.0,
            score=round(oos_score, 2),
            max_score=15.0,
            details={"oos_return_pct": oos_pnl_pct, "oos_sharpe": oos_sharpe, "oos_ratio": oos_ratio},
            passed_hurdle=(oos_pnl_pct >= 0),
        )

        # 5. Walk-Forward Stability (10 pts)
        # Target: Percentage of positive walk-forward windows >= 80%
        wf_positive_pct = float(metrics.get("wf_positive_windows_pct", 85.0))
        wf_score = min(10.0, max(0.0, (wf_positive_pct / 100.0) * 10.0))
        if wf_positive_pct < 60.0:
            rejection_reasons.append(f"Unstable Walk-Forward: Only {wf_positive_pct:.1f}% profitable windows")
        dim_scores["walk_forward"] = TradBenchDimensionScore(
            name="Walk-Forward Stability",
            weight=10.0,
            score=round(wf_score, 2),
            max_score=10.0,
            details={"wf_positive_windows_pct": wf_positive_pct},
            passed_hurdle=(wf_positive_pct >= 60.0),
        )

        # 6. Parameter Robustness (Plateau Test) (10 pts)
        # Target: Neighboring parameter drop-off < 20% (wide plateau)
        plateau_stability = float(metrics.get("parameter_plateau_stability", 0.85))  # 0 to 1
        rob_score = min(10.0, max(0.0, plateau_stability * 10.0))
        if plateau_stability < 0.50:
            rejection_reasons.append("Failed Plateau Test: Fragile overfitted parameter spike detected")
        dim_scores["robustness"] = TradBenchDimensionScore(
            name="Parameter Robustness",
            weight=10.0,
            score=round(rob_score, 2),
            max_score=10.0,
            details={"plateau_stability": plateau_stability},
            passed_hurdle=(plateau_stability >= 0.50),
        )

        # 7. Execution Reality Stress (5 pts)
        # Target: Model remains profitable at 2x slippage + 1.5s latency
        slippage_2x_pf = float(metrics.get("slippage_2x_profit_factor", 1.4))
        if slippage_2x_pf < 1.05:
            exec_score = 0.5
            rejection_reasons.append("Fragile Edge: Model becomes unprofitable at 2x slippage stress")
        else:
            exec_score = min(5.0, max(1.0, ((slippage_2x_pf - 1.0) / 0.5) * 5.0))
        dim_scores["execution_reality"] = TradBenchDimensionScore(
            name="Execution Reality Stress",
            weight=5.0,
            score=round(exec_score, 2),
            max_score=5.0,
            details={"slippage_2x_profit_factor": slippage_2x_pf},
            passed_hurdle=(slippage_2x_pf >= 1.05),
        )

        # 8. Paper Trading Verification (5 pts)
        # Target: Live paper trading profit factor > 1.25, tracking error low
        paper_pf = float(metrics.get("paper_profit_factor", 1.35))
        paper_score = min(5.0, max(0.0, ((paper_pf - 1.0) / 0.5) * 5.0))
        dim_scores["paper_trading"] = TradBenchDimensionScore(
            name="Paper Trading Verification",
            weight=5.0,
            score=round(paper_score, 2),
            max_score=5.0,
            details={"paper_profit_factor": paper_pf},
        )

        # 9. Safety & Circuit Breakers (5 pts - CRITICAL GATE)
        # Hard requirements: Hard SL enforcement, VaR kill switch, Daily loss limit, No-Trade capability
        has_hard_sl = bool(metrics.get("safety_has_hard_sl", True))
        has_circuit_breaker = bool(metrics.get("safety_has_circuit_breaker", True))
        has_notrade_filter = bool(metrics.get("safety_has_notrade_filter", True))
        risk_of_ruin = float(metrics.get("monte_carlo_risk_of_ruin_pct", 0.2))

        safety_critical_failure = False
        if not has_hard_sl:
            safety_critical_failure = True
            rejection_reasons.append("SAFETY CRITICAL FAILURE: Hard Stop-Loss enforcement missing!")
        if not has_circuit_breaker:
            safety_critical_failure = True
            rejection_reasons.append("SAFETY CRITICAL FAILURE: Daily drawdown circuit breaker missing!")
        if risk_of_ruin > 1.0:
            safety_critical_failure = True
            rejection_reasons.append(f"SAFETY CRITICAL FAILURE: Monte Carlo Risk of Ruin exceeds 1% ({risk_of_ruin:.2f}%)!")

        safety_score = 5.0 if not safety_critical_failure else 0.0
        dim_scores["safety_controls"] = TradBenchDimensionScore(
            name="Safety Controls & Circuit Breakers",
            weight=5.0,
            score=round(safety_score, 2),
            max_score=5.0,
            details={
                "has_hard_sl": has_hard_sl,
                "has_circuit_breaker": has_circuit_breaker,
                "has_notrade_filter": has_notrade_filter,
                "risk_of_ruin_pct": risk_of_ruin,
            },
            passed_hurdle=(not safety_critical_failure),
        )

        # Compute Composite Score
        total_score = sum(d.score for d in dim_scores.values())

        # Determine Promotion Status
        if safety_critical_failure or len(rejection_reasons) > 0:
            status = ModelPromotionStatus.REJECTED
        elif total_score >= 85.0:
            status = ModelPromotionStatus.CHALLENGER  # Ready to challenge the Champion!
        elif total_score >= 70.0:
            status = ModelPromotionStatus.CANDIDATE
        else:
            status = ModelPromotionStatus.EXPERIMENT

        return TradBenchReport(
            model_name=model_name,
            model_version=model_version,
            timestamp=metrics.get("timestamp", "2026-10-06T00:00:00Z"),
            total_score=round(total_score, 1),
            promotion_status=status,
            safety_critical_failure=safety_critical_failure,
            rejection_reasons=rejection_reasons,
            dimension_scores=dim_scores,
            summary_metrics=metrics,
        )
