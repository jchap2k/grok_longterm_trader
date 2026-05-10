"""Worker executed by the isolated Kronos Python environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Kronos worker.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kronos-root", required=True)
    parser.add_argument("--provider", choices=["yfinance"], default="yfinance")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--lookback", type=int, default=256)
    parser.add_argument("--pred-len", type=int, default=5)
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--device", default="cpu")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    kronos_root = Path(args.kronos_root)
    sys.path.insert(0, str(kronos_root))

    import pandas as pd
    import torch
    import yfinance as yf
    from model import Kronos, KronosPredictor, KronosTokenizer

    history = yf.Ticker(str(args.symbol).upper()).history(
        period=args.period,
        interval=args.interval,
        auto_adjust=False,
    )
    if history is None or history.empty:
        raise RuntimeError(f"No price history returned for {args.symbol}.")
    history = history.dropna(subset=["Open", "High", "Low", "Close"])
    lookback = max(1, int(args.lookback or 1))
    pred_len = max(1, int(args.pred_len or 1))
    if len(history) < lookback:
        raise RuntimeError(f"Need {lookback} price rows for {args.symbol}; got {len(history)}.")

    context = history.tail(lookback).copy()
    last_date = pd.Timestamp(context.index[-1]).tz_localize(None)
    future_dates = pd.bdate_range(last_date + pd.offsets.BDay(1), periods=pred_len)
    x_df = pd.DataFrame(
        {
            "open": context["Open"].astype(float).to_numpy(),
            "high": context["High"].astype(float).to_numpy(),
            "low": context["Low"].astype(float).to_numpy(),
            "close": context["Close"].astype(float).to_numpy(),
            "volume": context["Volume"].fillna(0).astype(float).to_numpy(),
        }
    )
    x_df["amount"] = x_df["close"] * x_df["volume"]
    x_timestamp = pd.Series(pd.to_datetime(context.index).tz_localize(None))
    y_timestamp = pd.Series(future_dates)

    load_started = time.time()
    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    tokenizer.eval()
    model.eval()
    load_seconds = time.time() - load_started

    predictor = KronosPredictor(model, tokenizer, device=args.device, max_context=512)
    predict_started = time.time()
    with torch.no_grad():
        pred_df = predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_len,
            T=1.0,
            top_k=1,
            top_p=1.0,
            sample_count=1,
            verbose=False,
        )
    predict_seconds = time.time() - predict_started

    forecast = []
    for index, row in pred_df.reset_index(drop=True).iterrows():
        forecast.append(
            {
                "date": str(y_timestamp.iloc[index].date()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )

    payload = {
        "symbol": str(args.symbol).upper(),
        "model": args.model,
        "tokenizer": args.tokenizer,
        "device": args.device,
        "lookback_rows": lookback,
        "last_close": float(x_df["close"].iloc[-1]),
        "history_start": str(pd.Timestamp(context.index[0]).date()),
        "history_end": str(pd.Timestamp(context.index[-1]).date()),
        "forecast": forecast,
        "timing_seconds": {
            "load_model": round(load_seconds, 3),
            "predict": round(predict_seconds, 3),
            "total": round(time.time() - started, 3),
        },
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
