from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://fapi.binance.com"
TIMEOUT = 12


@dataclass
class Market:
    symbol: str
    quote_volume: float
    change_24h: float
    funding_rate: float


class FuturesScanner:
    def __init__(
        self,
        quote_asset="USDT",
        interval="3m",
        trend_interval="15m",
        candle_limit=220,
        max_symbols=60,
        shortlist_size=20,
        min_quote_volume=20_000_000,
        workers=6,
    ):
        self.quote_asset = quote_asset
        self.interval = interval
        self.trend_interval = trend_interval
        self.candle_limit = max(120, candle_limit)
        self.max_symbols = max(10, max_symbols)
        self.shortlist_size = min(max(5, shortlist_size), self.max_symbols)
        self.min_quote_volume = min_quote_volume
        self.workers = max(2, min(workers, 10))

    @staticmethod
    def session():
        session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.35,
            status_forcelist=(418, 429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers["User-Agent"] = "futures-scalping-scanner/2.0"
        return session

    def get(self, path: str, params: dict | None = None):
        with self.session() as session:
            response = session.get(BASE_URL + path, params=params or {}, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()

    def markets(self) -> list[Market]:
        info = self.get("/fapi/v1/exchangeInfo")
        active = {
            x["symbol"]
            for x in info["symbols"]
            if x.get("status") == "TRADING"
            and x.get("contractType") == "PERPETUAL"
            and x.get("quoteAsset") == self.quote_asset
        }

        ticker = self.get("/fapi/v1/ticker/24hr")
        premium = self.get("/fapi/v1/premiumIndex")
        funding = {
            x["symbol"]: float(x.get("lastFundingRate", 0)) for x in premium
        }

        output = []
        for x in ticker:
            symbol = x.get("symbol")
            qv = float(x.get("quoteVolume", 0))
            if symbol not in active or qv < self.min_quote_volume:
                continue
            output.append(Market(
                symbol=symbol,
                quote_volume=qv,
                change_24h=float(x.get("priceChangePercent", 0)),
                funding_rate=funding.get(symbol, 0.0),
            ))

        output.sort(key=lambda m: m.quote_volume, reverse=True)
        return output[:self.max_symbols]

    def candles(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        raw = self.get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        cols = [
            "open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
        ]
        df = pd.DataFrame(raw, columns=cols)
        for c in ["open","high","low","close","volume","quote_volume","taker_buy_base"]:
            df[c] = df[c].astype(float)
        return df

    def open_interest_change(self, symbol: str) -> float:
        try:
            data = self.get("/futures/data/openInterestHist", {
                "symbol": symbol,
                "period": "5m",
                "limit": 3,
            })
            if not isinstance(data, list) or len(data) < 2:
                return 0.0
            old = float(data[-2].get("sumOpenInterestValue", 0) or 0)
            new = float(data[-1].get("sumOpenInterestValue", 0) or 0)
            return ((new / old) - 1) * 100 if old > 0 else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def rsi(close: pd.Series, period=14):
        d = close.diff()
        gain = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        loss = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        rs = gain / loss.replace(0, math.nan)
        return (100 - 100/(1+rs)).fillna(50.0)

    @staticmethod
    def atr(df: pd.DataFrame, period=14):
        prev = df["close"].shift(1)
        tr = pd.concat([
            df["high"]-df["low"],
            (df["high"]-prev).abs(),
            (df["low"]-prev).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()

    @staticmethod
    def prepare(df: pd.DataFrame):
        c = df["close"]
        df["ema7"] = c.ewm(span=7, adjust=False).mean()
        df["ema25"] = c.ewm(span=25, adjust=False).mean()
        df["ema99"] = c.ewm(span=99, adjust=False).mean()
        df["rsi"] = FuturesScanner.rsi(c)
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        df["macd"] = macd
        df["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
        df["atr"] = FuturesScanner.atr(df)
        df["volume_ma20"] = df["volume"].rolling(20, min_periods=5).mean()
        return df

    def quick_rank(self, market: Market):
        df = self.prepare(self.candles(market.symbol, self.interval, 120))
        r = df.iloc[-2]
        volume_ratio = float(r["volume"] / r["volume_ma20"]) if r["volume_ma20"] else 0
        movement = abs(((df.iloc[-2]["close"]/df.iloc[-7]["close"])-1)*100)
        volatility = float(r["atr"]/r["close"]*100)
        rank = volume_ratio*25 + movement*15 + volatility*12
        return market, rank

    def analyze(self, market: Market):
        fast = self.prepare(self.candles(market.symbol, self.interval, self.candle_limit))
        trend = self.prepare(self.candles(market.symbol, self.trend_interval, 140))
        oi_change = self.open_interest_change(market.symbol)

        r = fast.iloc[-2]
        p = fast.iloc[-3]
        t = trend.iloc[-2]
        price = float(r["close"])
        atr = float(r["atr"])
        if not atr or math.isnan(atr):
            return None

        vol_ratio = float(r["volume"]/r["volume_ma20"]) if r["volume_ma20"] else 0
        candle_pct = ((r["close"]-r["open"])/r["open"])*100
        momentum = ((fast.iloc[-2]["close"]/fast.iloc[-6]["close"])-1)*100

        long = short = 0
        lr, sr = [], []

        if r["ema7"] > r["ema25"]:
            long += 12; lr.append("EMA7 أعلى EMA25")
        else:
            short += 12; sr.append("EMA7 أسفل EMA25")

        if r["ema25"] > r["ema99"]:
            long += 14; lr.append("اتجاه الفريم السريع صاعد")
        else:
            short += 14; sr.append("اتجاه الفريم السريع هابط")

        if t["ema25"] > t["ema99"]:
            long += 18; lr.append("اتجاه 15 دقيقة صاعد")
        else:
            short += 18; sr.append("اتجاه 15 دقيقة هابط")

        if 50 <= r["rsi"] <= 68:
            long += 12; lr.append(f"RSI داعم {r['rsi']:.1f}")
        elif 32 <= r["rsi"] <= 50:
            short += 12; sr.append(f"RSI داعم {r['rsi']:.1f}")

        if r["macd"] > r["macd_signal"]:
            long += 10
            if p["macd"] <= p["macd_signal"]:
                long += 8; lr.append("تقاطع MACD صاعد")
        else:
            short += 10
            if p["macd"] >= p["macd_signal"]:
                short += 8; sr.append("تقاطع MACD هابط")

        if vol_ratio >= 1.35:
            if candle_pct > 0:
                long += 14; lr.append(f"فوليوم شراء ×{vol_ratio:.1f}")
            else:
                short += 14; sr.append(f"فوليوم بيع ×{vol_ratio:.1f}")

        if momentum > 0.25:
            long += 10; lr.append("زخم قصير صاعد")
        elif momentum < -0.25:
            short += 10; sr.append("زخم قصير هابط")

        if oi_change > 0.15:
            if candle_pct > 0:
                long += 8; lr.append(f"ارتفاع OI {oi_change:.2f}%")
            elif candle_pct < 0:
                short += 8; sr.append(f"ارتفاع OI {oi_change:.2f}%")

        funding_pct = market.funding_rate * 100
        if funding_pct > 0.05:
            short += 4; sr.append("Funding موجب ومرتفع")
        elif funding_pct < -0.05:
            long += 4; lr.append("Funding سالب ومرتفع")

        side = "LONG" if long > short else "SHORT"
        score = min(max(long, short), 100)
        reasons = lr if side == "LONG" else sr

        if score < 58:
            return None

        # تجنب مطاردة شمعة ممتدة جدًا
        extension = abs(price-r["ema7"])/price*100
        if extension > max(1.2, (atr/price*100)*2.2):
            score -= 10
            reasons.append("السعر ممتد؛ يفضل انتظار إعادة اختبار")

        if side == "LONG":
            stop = price - atr*1.1
            tp1 = price + atr*0.9
            tp2 = price + atr*1.6
        else:
            stop = price + atr*1.1
            tp1 = price - atr*0.9
            tp2 = price - atr*1.6

        return {
            "symbol": market.symbol,
            "side": side,
            "score": int(max(score, 0)),
            "interval": self.interval,
            "trend_interval": self.trend_interval,
            "entry_reference": round(price, 8),
            "stop_loss": round(stop, 8),
            "target_1": round(tp1, 8),
            "target_2": round(tp2, 8),
            "risk_reward_target_1": 0.82,
            "risk_reward_target_2": 1.45,
            "rsi": round(float(r["rsi"]), 2),
            "volume_ratio": round(vol_ratio, 2),
            "funding_rate_percent": round(funding_pct, 4),
            "open_interest_change_percent": round(oi_change, 3),
            "change_24h_percent": round(market.change_24h, 2),
            "quote_volume_24h": round(market.quote_volume, 2),
            "reasons": reasons[:6],
            "warning": "تحليل آلي وليس ضمانًا. لا يفتح البوت صفقات.",
        }

    def scan_market(self):
        markets = self.markets()
        if not markets:
            raise RuntimeError("لم يتم العثور على أسواق مطابقة لفلتر السيولة")
        ranked = []

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self.quick_rank, m): m for m in markets}
            for future in as_completed(futures):
                try:
                    ranked.append(future.result())
                except Exception:
                    pass

        if not ranked:
            raise RuntimeError("فشل تحميل بيانات الشموع لجميع الأسواق")
        ranked.sort(key=lambda x: x[1], reverse=True)
        shortlist = [m for m, _ in ranked[:self.shortlist_size]]

        signals = []
        with ThreadPoolExecutor(max_workers=max(3, self.workers//2)) as ex:
            futures = {ex.submit(self.analyze, m): m for m in shortlist}
            for future in as_completed(futures):
                try:
                    signal = future.result()
                    if signal:
                        signals.append(signal)
                except Exception:
                    pass

        signals.sort(key=lambda s: (s["score"], s["quote_volume_24h"]), reverse=True)
        return {"market_count": len(markets), "signals": signals}
