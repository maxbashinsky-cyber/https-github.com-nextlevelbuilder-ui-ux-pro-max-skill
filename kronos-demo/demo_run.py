"""Self-contained Kronos demo: forecast K-line (OHLCV) data on CPU.

Uses the bundled regression input data so no external dataset is needed.
Downloads the small pretrained model + tokenizer from Hugging Face on first run.
"""
import os
import random
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from model import Kronos, KronosTokenizer, KronosPredictor

SEED = 123
DEVICE = "cpu"
LOOKBACK = 400      # context length fed to the model
PRED_LEN = 120      # steps to forecast
FEATURES = ["open", "high", "low", "close", "volume", "amount"]

DATA_PATH = Path(__file__).parent / "tests" / "data" / "regression_input.csv"
OUT_PNG = Path(__file__).parent / "demo_forecast.png"


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def main():
    set_seed(SEED)

    print("Loading tokenizer + model from Hugging Face (first run downloads weights)...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    tokenizer.eval()
    model.eval()

    predictor = KronosPredictor(model, tokenizer, device=DEVICE, max_context=512)

    df = pd.read_csv(DATA_PATH, parse_dates=["timestamps"])
    print(f"Loaded {len(df)} rows of 5-min K-line data: "
          f"{df['timestamps'].iloc[0]} -> {df['timestamps'].iloc[-1]}")

    x_df = df.loc[: LOOKBACK - 1, FEATURES]
    x_ts = df.loc[: LOOKBACK - 1, "timestamps"]
    y_ts = df.loc[LOOKBACK : LOOKBACK + PRED_LEN - 1, "timestamps"]

    print(f"Forecasting {PRED_LEN} steps from a {LOOKBACK}-step context...")
    with torch.no_grad():
        pred_df = predictor.predict(
            df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=True,
        )

    pred_df.index = df["timestamps"].iloc[LOOKBACK : LOOKBACK + PRED_LEN].values

    # Ground truth for the forecast window (the data has it, so we can compare)
    truth = df.loc[LOOKBACK : LOOKBACK + PRED_LEN - 1].set_index("timestamps")
    mae = float(np.mean(np.abs(pred_df["close"].values - truth["close"].values)))

    print("\nForecast head:")
    print(pred_df[FEATURES].head())
    print(f"\nClose-price MAE vs ground truth over {PRED_LEN} steps: {mae:.4f}")

    # Plot close + volume: context, ground truth, prediction
    ctx = df.loc[: LOOKBACK - 1].set_index("timestamps")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

    ax1.plot(ctx.index, ctx["close"], color="gray", lw=1, label="Context")
    ax1.plot(truth.index, truth["close"], color="blue", lw=1.5, label="Ground Truth")
    ax1.plot(pred_df.index, pred_df["close"], color="red", lw=1.5, label="Kronos Prediction")
    ax1.axvline(ctx.index[-1], color="black", ls="--", lw=0.8)
    ax1.set_ylabel("Close")
    ax1.legend(loc="best")
    ax1.grid(alpha=0.3)
    ax1.set_title(f"Kronos-small forecast  |  close MAE={mae:.4f}")

    ax2.plot(ctx.index, ctx["volume"], color="gray", lw=1, label="Context")
    ax2.plot(truth.index, truth["volume"], color="blue", lw=1.5, label="Ground Truth")
    ax2.plot(pred_df.index, pred_df["volume"], color="red", lw=1.5, label="Kronos Prediction")
    ax2.axvline(ctx.index[-1], color="black", ls="--", lw=0.8)
    ax2.set_ylabel("Volume")
    ax2.legend(loc="best")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    print(f"\nSaved plot -> {OUT_PNG}")


if __name__ == "__main__":
    main()
