from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
TIMEOUT = 15


@dataclass
class MarketSymbol:
    symbol: str
    quote_volume: float
    price_change_percent: float


class FuturesScanner:
    def __init__(
        self,
        quote_asset: str = "USDT",
        interval: str = "5m",
        candle_limit: int = 220,
        max_symbols: int = 80,
        min_quote_volume: float = 25_000_000,
    ):
        self.quote_asset = quote_asset
        self.interval = interval
        self.candle_limit = candle_limit
        self.max_symbols = max_symbols
        self.min_quote_volume = min_quote_volume
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "binance-futures-signal-scanner/1.0"})

    def _get(self, path: str, params: dict | None = None) -> Any:
        response = self.session.get(
            f"{BASE_URL}{path}",
            params=params or {},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def active_symbols(self) -> set[str]:
        data = self._get("/fapi/v1/exchangeInfo")
        return {
            item["symbol"]
            for item in data["symbols"]
            if item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == self.quote_asset
        }

    def liquid_markets(self) -> list[MarketSymbol]:
        active = self.active_symbols()
        tickers = self._get("/fapi/v1/ticker/24hr")
        markets: list[MarketSymbol] = []

        for item in tickers:
            symbol = item.get("symbol", "")
            if symbol not in active:
                continue

            quote_volume = float(item.get("quoteVolume", 0))
            if quote_volume < self.min_quote_volume:
                continue

            markets.append(
                MarketSymbol(
                    symbol=symbol,
                    quote_volume=quote_volume,
                    price_change_percent=float(item.get("priceChangePercent", 0)),
                )
            )

        markets.sort(key=lambda x: x.quote_volume, reverse=True)
        return markets[: self.max_symbols]

    def candles(self, symbol: str) -> pd.DataFrame:
        raw = self._get(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": self.interval,
                "limit": self.candle_limit,
            },
        )
        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ]
        df = pd.DataFrame(raw, columns=columns)
        numeric = [
            "open", "high", "low", "close", "volume",
            "quote_volume", "taker_buy_base", "taker_buy_quote",
        ]
        df[numeric] = df[numeric].astype(float)
        return df

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, math.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        previous_close = df["close"].shift(1)
        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - previous_close).abs(),
                (df["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(alpha=1 / period, adjust=False).mean()

    def analyze(self, market: MarketSymbol) -> dict | None:
        df = self.candles(market.symbol)
        if len(df) < 120:
            return None

        close = df["close"]
        df["ema7"] = close.ewm(span=7, adjust=False).mean()
        df["ema25"] = close.ewm(span=25, adjust=False).mean()
        df["ema99"] = close.ewm(span=99, adjust=False).mean()
        df["rsi"] = self._rsi(close)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["atr"] = self._atr(df)
        df["volume_ma20"] = df["volume"].rolling(20).mean()

        row = df.iloc[-2]  # آخر شمعة مكتملة
        prev = df.iloc[-3]
        price = float(row["close"])
        atr = float(row["atr"])
        if not atr or math.isnan(atr) or price <= 0:
            return None

        volume_ratio = float(row["volume"] / row["volume_ma20"]) if row["volume_ma20"] else 0
        candle_change = ((row["close"] - row["open"]) / row["open"]) * 100
        atr_percent = (atr / price) * 100

        long_score = 0
        short_score = 0
        long_reasons: list[str] = []
        short_reasons: list[str] = []

        # اتجاه المتوسطات
        if row["ema7"] > row["ema25"] > row["ema99"]:
            long_score += 28
            long_reasons.append("ترتيب المتوسطات صاعد")
        elif row["ema7"] < row["ema25"] < row["ema99"]:
            short_score += 28
            short_reasons.append("ترتيب المتوسطات هابط")

        # موقع السعر
        if price > row["ema7"] and price > row["ema25"]:
            long_score += 14
            long_reasons.append("السعر أعلى EMA7 وEMA25")
        if price < row["ema7"] and price < row["ema25"]:
            short_score += 14
            short_reasons.append("السعر أسفل EMA7 وEMA25")

        # RSI بدون مطاردة مناطق التشبع
        if 52 <= row["rsi"] <= 68:
            long_score += 14
            long_reasons.append(f"RSI داعم {row['rsi']:.1f}")
        elif 32 <= row["rsi"] <= 48:
            short_score += 14
            short_reasons.append(f"RSI داعم {row['rsi']:.1f}")

        # MACD
        if row["macd"] > row["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
            long_score += 18
            long_reasons.append("تقاطع MACD صاعد")
        elif row["macd"] < row["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
            short_score += 18
            short_reasons.append("تقاطع MACD هابط")
        elif row["macd"] > row["macd_signal"]:
            long_score += 8
        elif row["macd"] < row["macd_signal"]:
            short_score += 8

        # الفوليوم والشمعة
        if volume_ratio >= 1.5:
            if candle_change > 0:
                long_score += 16
                long_reasons.append(f"فوليوم شراء مرتفع ×{volume_ratio:.1f}")
            elif candle_change < 0:
                short_score += 16
                short_reasons.append(f"فوليوم بيع مرتفع ×{volume_ratio:.1f}")

        # زخم آخر 3 شمعات
        momentum_3 = ((df.iloc[-2]["close"] / df.iloc[-5]["close"]) - 1) * 100
        if momentum_3 > 0.35:
            long_score += 10
            long_reasons.append("زخم قصير صاعد")
        elif momentum_3 < -0.35:
            short_score += 10
            short_reasons.append("زخم قصير هابط")

        side = "LONG" if long_score > short_score else "SHORT"
        score = int(min(max(long_score, short_score), 100))
        reasons = long_reasons if side == "LONG" else short_reasons

        if score < 55:
            return None

        # أهداف مبنية على ATR وليست ضمانًا
        if side == "LONG":
            stop = price - (1.15 * atr)
            target1 = price + (1.0 * atr)
            target2 = price + (1.8 * atr)
        else:
            stop = price + (1.15 * atr)
            target1 = price - (1.0 * atr)
            target2 = price - (1.8 * atr)

        return {
            "symbol": market.symbol,
            "side": side,
            "score": score,
            "interval": self.interval,
            "entry_reference": round(price, 8),
            "stop_loss": round(stop, 8),
            "target_1": round(target1, 8),
            "target_2": round(target2, 8),
            "rsi": round(float(row["rsi"]), 2),
            "volume_ratio": round(volume_ratio, 2),
            "atr_percent": round(atr_percent, 2),
            "change_24h_percent": round(market.price_change_percent, 2),
            "quote_volume_24h": round(market.quote_volume, 2),
            "reasons": reasons[:5],
            "warning": "إشارة تحليلية وليست ضمانًا أو أمرًا تلقائيًا.",
        }

    def scan_market(self) -> list[dict]:
        signals: list[dict] = []
        for market in self.liquid_markets():
            try:
                signal = self.analyze(market)
                if signal:
                    signals.append(signal)
            except (requests.RequestException, ValueError, KeyError):
                continue

        signals.sort(
            key=lambda item: (item["score"], item["quote_volume_24h"]),
            reverse=True,
        )
        return signals[:20]
