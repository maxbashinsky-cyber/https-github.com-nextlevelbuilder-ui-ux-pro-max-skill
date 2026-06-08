# Kronos K-line forecasting demo

A small, CPU-only demo of [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) —
an open-source foundation model for forecasting financial candlesticks (OHLCV K-lines).

> This folder is a self-contained runner. It does **not** vendor Kronos; it clones
> the upstream repo and uses the small pretrained model from Hugging Face.

## What it does

`demo_run.py`:
1. Loads `NeoQuasar/Kronos-Tokenizer-base` + `NeoQuasar/Kronos-small` (24.7M params) from Hugging Face.
2. Uses the **bundled regression data** in the Kronos repo (`tests/data/regression_input.csv`,
   2500 rows of 5-minute K-lines) as input — no external dataset needed.
3. Forecasts the next 120 steps from a 400-step context.
4. Prints the forecast head + close-price MAE vs. ground truth, and saves a plot
   (`demo_forecast.png`) comparing context / ground truth / prediction for close & volume.

## Run it

```bash
bash kronos-demo/run_kronos_demo.sh
```

This clones Kronos to `~/Kronos`, installs deps, and runs the demo.

## Network requirement (Claude Code on the web)

The pretrained weights live on **Hugging Face**, which is **not** in the default
`Trusted` network allowlist. Before running in a cloud session, edit the environment:

- **Network access** → **Custom**
- ✅ keep "Also include default list of common package managers" (so pip + git work)
- Add allowed domains:
  ```
  huggingface.co
  *.huggingface.co
  *.hf.co
  ```

Network-policy changes apply to **new** sessions, so save, then start a fresh session
on this branch and run the script.

Docs: https://code.claude.com/docs/en/claude-code-on-the-web#network-access

## Notes

- No GPU required; runs on CPU (`device="cpu"` in `demo_run.py`).
- `torch` is installed from PyPI because the `download.pytorch.org` CPU index is
  not in the allowlist; the PyPI wheel runs fine on CPU.
- Upstream Kronos is MIT-licensed.
