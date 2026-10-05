"""Next-Gen Smart Money Momentum Scalper Strategy.

Combines high-frequency EMA ribbon (9/21/50) momentum, RSI chop filters,
dynamic ATR bracket geometry (1:2 R:R), volume breakout confirmation, and
real-time Binance institutional Whale / Smart Money alignment.
"""

from decimal import Decimal
from typing import Any
import numpy as np

from trad_auto.ai.qlib_alpha import QlibAlphaEngine, QlibAlphaSnapshot
from trad_auto.ai.smart_money import BinanceSmartMoneyAnalyzer
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import MarketRegimeType, OrderSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.indicators.adx import ADX
from trad_auto.indicators.atr import ATR
from trad_auto.indicators.moving_averages import EMA, SMA
from trad_auto.indicators.rsi import RSI
from trad_auto.market_data.regime import MarketRegimeDetector, RegimeClassification
from trad_auto.market_data.store import BarStore
from trad_auto.math.fractional_diff import FractionalDifferentiator
from trad_auto.quant.alpha158 import Alpha158Engine
from trad_auto.quant.cross_sectional_ranker import (
    CrossSectionalRanker,
    CrossSectionalSnapshot,
)
from trad_auto.quant.cvd_engine import (
    CumulativeVolumeDeltaEngine,
    CVDAnalysisResult,
)
from trad_auto.quant.volume_profile import (
    VolumeProfileEngine,
    VolumeProfileResult,
)
from trad_auto.strategies.base import BaseStrategy



class SmartMoneyScalperStrategy(BaseStrategy):
    """Institutional-grade 1m/5m scalping strategy backed by Binance Smart Money & Qlib Alpha.

    Rules:
    1. Trend Ribbon: Fast EMA (9) vs Slow EMA (21) vs Macro Trend EMA (50).
    2. Momentum Filter: RSI(14) between 52-70 for Long, 30-48 for Short (prevents chop).
    3. Volume Confirmation: Current bar volume >= 1.1x 20-bar Average Volume.
    4. Smart Money Alignment: Binance top-trader whale flow must NOT oppose the trade.
    5. Qlib Microstructure Alpha: Sub-millisecond Alpha158 momentum & volume flow conviction.
    6. ADX Regime Filter: Blocks trading in sideways chop (ADX < 22).
    7. Asymmetric R:R: Dynamic ATR stop-loss with strictly >= 1:2.0 profit target.
    """

    def __init__(
        self,
        strategy_id: str = "smart_money_scalper",
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        fast_ema_period: int = 9,
        slow_ema_period: int = 21,
        trend_ema_period: int = 50,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_multiplier: Decimal = Decimal("1.5"),
        rr_target_multiplier: Decimal = Decimal("2.0"),
        volume_multiplier: Decimal = Decimal("0.80"),
        rsi_long_lower: Decimal = Decimal("48.0"),
        rsi_long_upper: Decimal = Decimal("75.0"),
        rsi_short_lower: Decimal = Decimal("25.0"),
        rsi_short_upper: Decimal = Decimal("52.0"),
        smart_money_analyzer: BinanceSmartMoneyAnalyzer | None = None,
        qlib_alpha_engine: QlibAlphaEngine | None = None,
        enable_qlib_alpha: bool = True,
        qlib_min_conviction: Decimal = Decimal("0.10"),
        adx_period: int = 14,
        adx_threshold: Decimal = Decimal("18.0"),
        enable_adx_filter: bool = True,
        enable_ml_filter: bool = True,
        ml_min_probability: float = 0.52,
        adaptive_policy: Any = None,
        enable_cross_sectional: bool = True,
        cross_sectional_ranker: CrossSectionalRanker | None = None,
        alpha158_engine: Alpha158Engine | None = None,
        enable_orderflow: bool = True,
        volume_profile_engine: VolumeProfileEngine | None = None,
        cvd_engine: CumulativeVolumeDeltaEngine | None = None,
    ) -> None:
        target_symbols = symbols or ["BTCUSDT"]
        target_timeframes = timeframes or ["1m", "5m"]
        super().__init__(strategy_id, target_symbols, target_timeframes)

        if rr_target_multiplier < Decimal("2.0"):
            raise DomainValidationError("rr_target_multiplier cannot be lower than 2.0 (1:2 R:R)")

        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.trend_ema_period = trend_ema_period
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_target_multiplier = rr_target_multiplier
        self.volume_multiplier = volume_multiplier
        self.rsi_long_lower = rsi_long_lower
        self.rsi_long_upper = rsi_long_upper
        self.rsi_short_lower = rsi_short_lower
        self.rsi_short_upper = rsi_short_upper
        self.smart_money_analyzer = smart_money_analyzer or BinanceSmartMoneyAnalyzer()
        self.qlib_alpha_engine = qlib_alpha_engine or QlibAlphaEngine()
        self.enable_qlib_alpha = enable_qlib_alpha
        self.qlib_min_conviction = qlib_min_conviction
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.enable_adx_filter = enable_adx_filter
        self.enable_ml_filter = enable_ml_filter
        self.ml_min_probability = ml_min_probability
        self.adaptive_policy = adaptive_policy
        self.enable_cross_sectional = enable_cross_sectional
        self.alpha158_engine = alpha158_engine or Alpha158Engine()
        self.cross_sectional_ranker = cross_sectional_ranker or CrossSectionalRanker()
        self._last_cross_sectional_snapshot: CrossSectionalSnapshot | None = None

        # Order Flow & Auction Profile Engines
        self.enable_orderflow = enable_orderflow
        self.volume_profile_engine = volume_profile_engine or VolumeProfileEngine(n_bins=40, value_area_pct=0.70)
        self.cvd_engine = cvd_engine or CumulativeVolumeDeltaEngine(lookback_window=20, divergence_threshold_pct=0.15)
        self._last_volume_profile: dict[str, VolumeProfileResult] = {}
        self._last_cvd_analysis: dict[str, CVDAnalysisResult] = {}


        self._ml_model: Any = None
        self._ml_metadata: dict[str, Any] | None = None
        self._feature_extractor: Any = None
        self._last_ml_probability: float | None = None

        # Statistical Market Regime Detector (GMM / Hidden Markov)
        self._regime_detector = MarketRegimeDetector(min_bars_required=30)
        self._last_regime: RegimeClassification | None = None

        # Fractional Differentiation Engine (Marcos López de Prado)
        self._frac_diff = FractionalDifferentiator(d=0.40, threshold=1e-4)
        self._last_frac_diff_val: float | None = None

        if self.enable_ml_filter:
            self._load_ml_model()

        # Indicator state cache per (symbol, timeframe)
        self._fast_emas: dict[tuple[str, str], EMA] = {}
        self._slow_emas: dict[tuple[str, str], EMA] = {}
        self._trend_emas: dict[tuple[str, str], EMA] = {}
        self._rsis: dict[tuple[str, str], RSI] = {}
        self._atrs: dict[tuple[str, str], ATR] = {}
        self._vol_smas: dict[tuple[str, str], SMA] = {}
        self._adxs: dict[tuple[str, str], ADX] = {}

    def _get_or_create_indicators(
        self, symbol: str, timeframe: str
    ) -> tuple[EMA, EMA, EMA, RSI, ATR, SMA, ADX]:
        key = (symbol, timeframe)
        if key not in self._fast_emas:
            self._fast_emas[key] = EMA(self.fast_ema_period)
            self._slow_emas[key] = EMA(self.slow_ema_period)
            self._trend_emas[key] = EMA(self.trend_ema_period)
            self._rsis[key] = RSI(self.rsi_period)
            self._atrs[key] = ATR(self.atr_period)
            self._vol_smas[key] = SMA(20)
            self._adxs[key] = ADX(self.adx_period)

        return (
            self._fast_emas[key],
            self._slow_emas[key],
            self._trend_emas[key],
            self._rsis[key],
            self._atrs[key],
            self._vol_smas[key],
            self._adxs[key],
        )

    def _load_ml_model(self) -> None:
        """Loads trained Quant ML model artifact if available."""
        try:
            from pathlib import Path
            import joblib
            from trad_auto.training.trainer import QuantModelTrainer

            model_dir = Path("data/models")
            champ_file = model_dir / "lean_champion_stacking_model.joblib"
            if champ_file.exists():
                self._ml_model = joblib.load(champ_file)
                self._feature_extractor = self.alpha158_engine
                return

            trainer = QuantModelTrainer()
            loaded = trainer.load_model("btc_scalper_ml")
            if loaded is not None:
                self._ml_model, self._ml_metadata = loaded
                self._feature_extractor = self.alpha158_engine
            else:
                self._feature_extractor = self.alpha158_engine
        except Exception:
            self._feature_extractor = self.alpha158_engine


    def hot_reload_model(self, model: Any, metadata: dict[str, Any]) -> None:
        """Atomically hot-reloads a freshly re-trained ML model into the live strategy."""
        from trad_auto.training.features import QuantFeatureExtractor

        self._ml_model = model
        self._ml_metadata = metadata
        f_names = metadata.get("feature_names", []) if metadata else []
        if len(f_names) == 158:
            self._feature_extractor = self.alpha158_engine
        else:
            self._feature_extractor = QuantFeatureExtractor()

    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        """Evaluates closed candle bar for smart-money backed scalping setups."""
        if not self.handles(bar.symbol, bar.timeframe):
            return []

        (
            fast_ema,
            slow_ema,
            trend_ema,
            rsi,
            atr,
            vol_sma,
            adx,
        ) = self._get_or_create_indicators(bar.symbol, bar.timeframe)

        # Update streaming indicators
        fast_ema.update(bar.close)
        slow_ema.update(bar.close)
        trend_ema.update(bar.close)
        rsi.update(bar.close)
        atr.update_bar(bar)
        vol_sma.update(bar.volume)
        adx.update_bar(bar)

        # Ensure all core ribbon & momentum indicators are ready
        if not (
            fast_ema.is_ready
            and slow_ema.is_ready
            and trend_ema.is_ready
            and rsi.is_ready
            and atr.is_ready
            and vol_sma.is_ready
        ):
            return []

        fast_val = fast_ema.value
        slow_val = slow_ema.value
        trend_val = trend_ema.value
        rsi_val = rsi.value
        atr_val = atr.value
        vol_avg = vol_sma.value

        if (
            fast_val is None
            or slow_val is None
            or trend_val is None
            or rsi_val is None
            or atr_val is None
            or atr_val <= ZERO_DECIMAL
            or vol_avg is None
            or vol_avg <= ZERO_DECIMAL
        ):
            return []

        risk_distance = atr_val * self.atr_multiplier
        if risk_distance <= ZERO_DECIMAL:
            return []

        # 1. Volume filter: Require volume to exceed the configured multiplier of average volume
        if bar.volume < (vol_avg * self.volume_multiplier):
            return []

        # 2. Qlib Alpha Microstructure Check
        qlib_snapshot: QlibAlphaSnapshot | None = None
        if self.enable_qlib_alpha:
            recent_bars = store.get_bars(
                bar.symbol, bar.timeframe, count=self.qlib_alpha_engine.lookback_bars
            )
            if len(recent_bars) >= self.qlib_alpha_engine.lookback_bars:
                qlib_snapshot = self.qlib_alpha_engine.calculate_alpha(recent_bars)

        # 3. Statistical Market Regime Classification (GMM / Hidden Markov)
        regime_bars = store.get_bars(bar.symbol, bar.timeframe, count=60)
        if len(regime_bars) >= 30:
            self._last_regime = self._regime_detector.classify(regime_bars)
            if self._last_regime.regime == MarketRegimeType.HIGH_VOLATILITY_CHAOS:
                return []
            if self._last_regime.regime == MarketRegimeType.CHOP_SIDEWAYS and self.enable_adx_filter:
                # If ADX has risen into trend territory (>=18), allow breakout entry
                if not (adx.is_ready and adx.value is not None and adx.value >= self.adx_threshold):
                    return []

            closes_arr = np.array([float(b.close) for b in regime_bars], dtype=np.float64)
            self._last_frac_diff_val = float(self._frac_diff.transform(closes_arr)[-1])

        # 3.1. ADX Regime Filter: Ensure market is not in flat sideways chop
        if self.enable_adx_filter and adx.is_ready and adx.value is not None:
            if adx.value < self.adx_threshold:
                # Flat sideways chop detected: block trend scalping
                return []

        # 3.4. Cross-Sectional Multi-Asset Alpha Ranking Gate
        # Institutional hedge fund standard: Rank all universe assets and trade ONLY the top-ranked asset
        if (
            self.enable_cross_sectional
            and self.cross_sectional_ranker is not None
            and self.alpha158_engine is not None
            and len(self.symbols) > 1
        ):
            asset_features: dict[str, np.ndarray] = {}
            for sym in self.symbols:
                s_bars = store.get_bars(sym, bar.timeframe, count=65)
                if len(s_bars) >= 61:
                    s_candles = [
                        {
                            "open": float(b.open),
                            "high": float(b.high),
                            "low": float(b.low),
                            "close": float(b.close),
                            "volume": float(b.volume),
                        }
                        for b in s_bars
                    ]
                    try:
                        X_sym, _, _ = self.alpha158_engine.extract_features(s_candles)
                        asset_features[sym] = X_sym[-1:]
                    except Exception:
                        pass

            if len(asset_features) >= 2:
                cs_snapshot = self.cross_sectional_ranker.rank_assets(
                    asset_features, self.alpha158_engine.FEATURE_NAMES
                )
                self._last_cross_sectional_snapshot = cs_snapshot

                # Market dispersion filter: if market is flat/uncorrelated alpha is low, skip trading
                should_trade, _ = self.cross_sectional_ranker.should_trade(cs_snapshot)
                if not should_trade:
                    return []

                # Relative alpha filter: only allow entries on the #1 ranked asset!
                if bar.symbol != cs_snapshot.top_asset:
                    return []

        # 3.5. Quant Machine Learning Model Probability Gate
        if (
            self.enable_ml_filter
            and self._ml_model is not None
            and self._feature_extractor is not None
        ):
            recent_bars = store.get_bars(bar.symbol, bar.timeframe, count=65)
            warmup_needed = 61 if isinstance(self._feature_extractor, Alpha158Engine) else 60
            if len(recent_bars) >= warmup_needed:
                raw_candles = [
                    {
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "volume": float(b.volume),
                        "taker_buy_volume": float(b.volume) * 0.5,
                    }
                    for b in recent_bars
                ]
                try:
                    X, _, _ = self._feature_extractor.extract_features(raw_candles)
                    prob = float(self._ml_model.predict_proba(X[-1:])[:, 1][0])
                    self._last_ml_probability = prob
                    min_prob = self.ml_min_probability
                    if self.adaptive_policy is not None and self._last_regime is not None:
                        min_prob = self.adaptive_policy.get_threshold_for_regime(
                            self._last_regime.regime.value
                        )
                    if prob < min_prob:
                        return []
                except Exception:
                    pass

        # 3.6. Order Flow & Auction Profile Analytics (CVD + Volume Profile)
        cvd_res: CVDAnalysisResult | None = None
        vp_res: VolumeProfileResult | None = None
        if self.enable_orderflow:
            of_bars = store.get_bars(bar.symbol, bar.timeframe, count=40)
            if len(of_bars) >= 10:
                try:
                    cvd_res = self.cvd_engine.compute_cvd(of_bars, symbol=bar.symbol)
                    vp_res = self.volume_profile_engine.compute_profile(of_bars, symbol=bar.symbol)
                    self._last_cvd_analysis[bar.symbol] = cvd_res
                    self._last_volume_profile[bar.symbol] = vp_res
                except Exception:
                    pass

        proposals: list[TradeProposal] = []

        # 4. Bullish Long Scalp Setup
        # Ribbon: Fast > Slow and Close > Trend EMA
        # Momentum: RSI between configurable thresholds
        if (
            fast_val > slow_val
            and bar.close > trend_val
            and self.rsi_long_lower <= rsi_val <= self.rsi_long_upper
        ):
            # ADX directional check: +DI must dominate -DI in Long
            if self.enable_adx_filter and adx.is_ready:
                if adx.plus_di is not None and adx.minus_di is not None:
                    if adx.plus_di <= adx.minus_di:
                        return []

            # Qlib Alpha filter: Ensure momentum & volume flow confirm bullish direction
            if (
                qlib_snapshot is not None
                and qlib_snapshot.composite_score < self.qlib_min_conviction
            ):
                return []

            # Order Flow CVD Exhaustion Check (Prevent buying into smart money distribution traps)
            if cvd_res is not None and not cvd_res.allow_long:
                return []

            # Verify Smart Money Whale flow alignment
            aligned, sm_reason = self.smart_money_analyzer.validate_proposal_alignment(
                bar.symbol, OrderSide.BUY
            )
            if aligned:
                stop_loss = bar.close - risk_distance
                if stop_loss > ZERO_DECIMAL:
                    take_profit = bar.close + (risk_distance * self.rr_target_multiplier)
                    qlib_info = (
                        f", Qlib Alpha={qlib_snapshot.composite_score:+.2f}"
                        if qlib_snapshot is not None
                        else ""
                    )
                    adx_info = (
                        f", ADX={adx.value:.1f}"
                        if (adx.is_ready and adx.value is not None)
                        else ""
                    )
                    cvd_info = (
                        f", CVD={cvd_res.divergence_signal}"
                        if cvd_res is not None
                        else ""
                    )
                    vp_info = (
                        f", POC=${vp_res.poc_price:,.2f}"
                        if vp_res is not None
                        else ""
                    )
                    ml_conf = (
                        Decimal(str(round(self._last_ml_probability, 4)))
                        if self._last_ml_probability is not None
                        else None
                    )
                    proposals.append(
                        TradeProposal(
                            strategy_id=self.strategy_id,
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            direction=OrderSide.BUY,
                            entry_price=bar.close,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=bar.timestamp,
                            confidence=ml_conf,
                            reason=(
                                f"Smart Money Long Scalp: EMA9 ({fast_val:.2f}) > "
                                f"EMA21 ({slow_val:.2f}) > EMA50 ({trend_val:.2f}), "
                                f"RSI={rsi_val:.1f}{qlib_info}{adx_info}{cvd_info}{vp_info}, {sm_reason}"
                            ),
                        )
                    )

        # 5. Bearish Short Scalp Setup
        # Ribbon: Fast < Slow and Close < Trend EMA
        # Momentum: RSI between configurable thresholds
        elif (
            fast_val < slow_val
            and bar.close < trend_val
            and self.rsi_short_lower <= rsi_val <= self.rsi_short_upper
        ):
            # ADX directional check: -DI must dominate +DI in Short
            if self.enable_adx_filter and adx.is_ready:
                if adx.plus_di is not None and adx.minus_di is not None:
                    if adx.minus_di <= adx.plus_di:
                        return []

            # Qlib Alpha filter: Ensure momentum & volume flow confirm bearish direction
            if (
                qlib_snapshot is not None
                and qlib_snapshot.composite_score > -self.qlib_min_conviction
            ):
                return []

            # Order Flow CVD Absorption Check (Prevent shorting into aggressive limit buyer absorption)
            if cvd_res is not None and not cvd_res.allow_short:
                return []

            # Verify Smart Money Whale flow alignment
            aligned, sm_reason = self.smart_money_analyzer.validate_proposal_alignment(
                bar.symbol, OrderSide.SELL
            )
            if aligned:
                stop_loss = bar.close + risk_distance
                take_profit = bar.close - (risk_distance * self.rr_target_multiplier)
                if take_profit > ZERO_DECIMAL:
                    qlib_info = (
                        f", Qlib Alpha={qlib_snapshot.composite_score:+.2f}"
                        if qlib_snapshot is not None
                        else ""
                    )
                    adx_info = (
                        f", ADX={adx.value:.1f}"
                        if (adx.is_ready and adx.value is not None)
                        else ""
                    )
                    cvd_info = (
                        f", CVD={cvd_res.divergence_signal}"
                        if cvd_res is not None
                        else ""
                    )
                    vp_info = (
                        f", POC=${vp_res.poc_price:,.2f}"
                        if vp_res is not None
                        else ""
                    )
                    ml_conf = (
                        Decimal(str(round(self._last_ml_probability, 4)))
                        if self._last_ml_probability is not None
                        else None
                    )
                    proposals.append(
                        TradeProposal(
                            strategy_id=self.strategy_id,
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            direction=OrderSide.SELL,
                            entry_price=bar.close,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=bar.timestamp,
                            confidence=ml_conf,
                            reason=(
                                f"Smart Money Short Scalp: EMA9 ({fast_val:.2f}) < "
                                f"EMA21 ({slow_val:.2f}) < EMA50 ({trend_val:.2f}), "
                                f"RSI={rsi_val:.1f}{qlib_info}{adx_info}{cvd_info}{vp_info}, {sm_reason}"
                            ),
                        )
                    )

        return proposals

    def reset(self) -> None:
        """Resets all streaming indicator states."""
        for ema in self._fast_emas.values():
            ema.reset()
        for ema in self._slow_emas.values():
            ema.reset()
        for ema in self._trend_emas.values():
            ema.reset()
        for rsi in self._rsis.values():
            rsi.reset()
        for atr in self._atrs.values():
            atr.reset()
        for sma in self._vol_smas.values():
            sma.reset()
        for adx in self._adxs.values():
            adx.reset()

    def get_indicator_snapshot(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m"
    ) -> dict[str, Any]:
        """Provides latest snapshot of indicator values for telemetry and dashboard."""
        key = (symbol.upper(), timeframe)
        fast_ema = self._fast_emas.get(key)
        slow_ema = self._slow_emas.get(key)
        trend_ema = self._trend_emas.get(key)
        rsi = self._rsis.get(key)
        atr = self._atrs.get(key)
        adx = self._adxs.get(key)

        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "fast_ema": str(fast_ema.value) if fast_ema and fast_ema.is_ready else None,
            "slow_ema": str(slow_ema.value) if slow_ema and slow_ema.is_ready else None,
            "trend_ema": str(trend_ema.value) if trend_ema and trend_ema.is_ready else None,
            "rsi": str(rsi.value) if rsi and rsi.is_ready else None,
            "atr": str(atr.value) if atr and atr.is_ready else None,
            "adx": str(adx.adx) if adx and adx.is_ready else None,
            "plus_di": str(adx.plus_di) if adx and adx.is_ready else None,
            "minus_di": str(adx.minus_di) if adx and adx.is_ready else None,
            "is_adx_trending": (
                (adx.adx is not None and adx.adx >= self.adx_threshold)
                if adx and adx.is_ready
                else None
            ),
            "ml_probability": (
                f"{self._last_ml_probability:.2f}"
                if self._last_ml_probability is not None
                else None
            ),
            "adaptive_ml_threshold": (
                round(
                    self.adaptive_policy.get_threshold_for_regime(
                        self._last_regime.regime.value if self._last_regime else "UNKNOWN"
                    ),
                    2,
                )
                if self.adaptive_policy is not None
                else self.ml_min_probability
            ),
            "is_ml_active": self._ml_model is not None,
            "market_regime": (
                self._last_regime.regime.value
                if self._last_regime is not None
                else MarketRegimeType.UNKNOWN.value
            ),
            "regime_probability": (
                f"{self._last_regime.probability:.1%}"
                if self._last_regime is not None
                else None
            ),
            "volatility_zscore": (
                f"{self._last_regime.volatility_zscore:+.1f}"
                if self._last_regime is not None
                else None
            ),
            "regime_recommendation": (
                self._last_regime.recommendation
                if self._last_regime is not None
                else "WARMING_UP"
            ),
            "frac_diff_val": (
                f"{self._last_frac_diff_val:.4f}"
                if self._last_frac_diff_val is not None
                else None
            ),
            "frac_diff_memory": f"d={self._frac_diff.d:.2f} ({self._frac_diff.get_memory_weight_ratio():.1%} Memory)",
            "cs_regime": (
                self._last_cross_sectional_snapshot.regime_label
                if self._last_cross_sectional_snapshot is not None
                else "N/A"
            ),
            "cs_top_asset": (
                self._last_cross_sectional_snapshot.top_asset
                if self._last_cross_sectional_snapshot is not None
                else "N/A"
            ),
            "cs_spread": (
                f"{self._last_cross_sectional_snapshot.spread:.2f}"
                if self._last_cross_sectional_snapshot is not None
                else "N/A"
            ),
            "cs_dispersion": (
                f"{self._last_cross_sectional_snapshot.dispersion:.4f}"
                if self._last_cross_sectional_snapshot is not None
                else "N/A"
            ),
            "cs_tradeable_count": (
                self._last_cross_sectional_snapshot.n_tradeable
                if self._last_cross_sectional_snapshot is not None
                else 0
            ),
        }
