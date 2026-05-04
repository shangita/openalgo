PARAM Capital Backtest — Data Directory
========================================

Required CSV files (place here before running):
  GOLDM_5min.csv       — MCX Gold Mini, 5-minute bars
  SILVERMICM_1min.csv  — MCX Silver Micro, 1-minute bars
  EURUSD_M15.csv       — FX EUR/USD, 15-minute bars

Expected CSV columns (case-insensitive):
  datetime  — ISO format: 2024-01-02 09:15:00
  open
  high
  low
  close
  volume    — optional; defaults to 1 if absent

The engine auto-detects the datetime column and sets it as the index.
Timezone: naive timestamps assumed to be exchange-local time.

Data sources:
  MCX: Zerodha Kite historical API or NSE/MCX data vendors
  FX:  Dukascopy, FXCM, or broker MT4/MT5 exports
