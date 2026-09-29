"""CLI entrypoint to train the BTCUSDT Smart Money Scalper Quant ML Model."""

import argparse
import logging
import sys

from trad_auto.training.trainer import QuantModelTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("trad_auto.training.cli")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trad-Auto: Train Quantitative Machine Learning Model for BTCUSDT"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading pair symbol (default: BTCUSDT)",
    )
    parser.add_argument(
        "--candles",
        type=int,
        default=3000,
        help="Number of historical candles to train on (default: 3000)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.55,
        help="Decision probability threshold (default: 0.55)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        default=False,
        help="Force use of synthetic data generator instead of live Binance API",
    )
    args = parser.parse_args()

    print("\n" + "=" * 75)
    print("  TRAD-AUTO INSTITUTIONAL QUANT AI TRAINING PIPELINE")
    print(f"  Target Asset: {args.symbol} (Binance USD-M Futures)")
    print(f"  Sample Size:  {args.candles} candles")
    print(f"  Conviction:   >= {args.threshold * 100:.0f}%")
    print("=" * 75 + "\n")

    trainer = QuantModelTrainer(decision_threshold=args.threshold)

    candles = None
    if args.synthetic:
        logger.info("Generating %d synthetic market regime candles...", args.candles)
        candles = trainer.downloader.generate_synthetic_klines(
            symbol=args.symbol, count=args.candles
        )

    result = trainer.train_pipeline(candles=candles, candle_count=args.candles)

    perf = result.performance
    print("\n" + "=" * 75)
    print("  QUANTITATIVE MODEL EVALUATION REPORT (Walk-Forward Out-Of-Sample)")
    print("=" * 75)
    print(f"  • Total Executed Trades:   {perf.total_trades}")
    print(f"  • Win Rate:                {perf.win_rate_pct:.1f}%")
    print(f"  • Profit Factor:           {perf.profit_factor:.2f}")
    print(f"  • Total Net Return:        {perf.total_net_return_pct:+.2f}%")
    print(f"  • Max Drawdown:            {perf.max_drawdown_pct:.2f}%")
    print(f"  • Annualized Sharpe Ratio: {perf.sharpe_ratio:.2f}")
    print("-" * 75)
    print(f"  Model Saved:    {result.model_path}")
    print(f"  Metadata Saved: {result.metadata_path}")
    print("=" * 75 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
