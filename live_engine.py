from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import websocket

MARKET_BASE = "wss://fstream.binance.com/market/stream?streams="

DEFAULT_SYMBOLS = [
    "btcusdt","ethusdt","bnbusdt","solusdt","xrpusdt",
    "dogeusdt","adausdt","suiusdt","linkusdt","avaxusdt",
    "ltcusdt","trxusdt","dotusdt","1000pepeusdt","1000shibusdt",
    "aptusdt","arbusdt","opusdt","nearusdt","atomusdt",
]


class LiveEngine:
    def __init__(self, notifier, min_score=72, cooldown_minutes=30):
        self.notifier = notifier
        self.min_score = min_score
        self.cooldown = timedelta(minutes=cooldown_minutes)

        self.symbols = DEFAULT_SYMBOLS
        self.candles_1m = defaultdict(lambda: deque(maxlen=120))
        self.candles_5m = defaultdict(lambda: deque(maxlen=120))
        self.funding = defaultdict(float)
        self.signals = deque(maxlen=20)
        self.last_notified = {}

        self.ws = None
        self.thread = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()

        self.websocket_connected = False
        self.last_message_at = None
        self.last_error = None
        self.message_count = 0
        self.reconnect_count = 0

    @staticmethod
    def now_iso():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def build_url(self):
        streams = []
        for symbol in self.symbols:
            streams.append(f"{symbol}@kline_1m")
            streams.append(f"{symbol}@kline_5m")
            streams.append(f"{symbol}@markPrice@1s")
        return MARKET_BASE + "/".join(streams)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run_forever, daemon=True, name="binance-ws")
            self.thread.start()

    def restart(self):
        self.stop_event.set()
        with self.lock:
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
        time.sleep(1)
        self.stop_event.clear()
        self.start()

    def _run_forever(self):
        backoff = 3
        while not self.stop_event.is_set():
            try:
                self.ws = websocket.WebSocketApp(
                    self.build_url(),
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever(
                    ping_interval=150,
                    ping_timeout=20,
                    reconnect=0,
                )
            except Exception as exc:
                with self.lock:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.websocket_connected = False

            if self.stop_event.is_set():
                break

            with self.lock:
                self.reconnect_count += 1
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_open(self, ws):
        with self.lock:
            self.websocket_connected = True
            self.last_error = None

    def _on_close(self, ws, close_status_code, close_msg):
        with self.lock:
            self.websocket_connected = False
            if close_status_code or close_msg:
                self.last_error = f"WebSocket closed: {close_status_code} {close_msg}"

    def _on_error(self, ws, error):
        with self.lock:
            self.websocket_connected = False
            self.last_error = f"WebSocket error: {error}"

    def _on_message(self, ws, raw_message):
        try:
            payload = json.loads(raw_message)
            data = payload.get("data", payload)
            event = data.get("e")

            with self.lock:
                self.last_message_at = self.now_iso()
                self.message_count += 1

            if event == "markPriceUpdate":
                symbol = data["s"].lower()
                self.funding[symbol] = float(data.get("r", 0) or 0) * 100
                return

            if event != "kline":
                return

            symbol = data["s"].lower()
            kline = data["k"]
            interval = kline["i"]

            # نعتمد فقط على الشمعة المكتملة لمنع الإشارات المتغيرة.
            if not kline.get("x"):
                return

            candle = {
                "open_time": int(kline["t"]),
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": float(kline["c"]),
                "volume": float(kline["v"]),
            }

            bucket = self.candles_1m[symbol] if interval == "1m" else self.candles_5m[symbol]

            # منع تكرار نفس الشمعة بعد إعادة الاتصال.
            if bucket and bucket[-1]["open_time"] == candle["open_time"]:
                bucket[-1] = candle
            else:
                bucket.append(candle)

            if interval == "1m":
                signal = self.analyze(symbol)
                if signal:
                    self._save_and_notify(signal)

        except Exception as exc:
            with self.lock:
                self.last_error = f"Message processing: {type(exc).__name__}: {exc}"

    @staticmethod
    def ema(values, period):
        if not values:
            return 0.0
        multiplier = 2 / (period + 1)
        result = values[0]
        for value in values[1:]:
            result = (value - result) * multiplier + result
        return result

    @staticmethod
    def rsi(values, period=14):
        if len(values) <= period:
            return 50.0
        gains, losses = [], []
        for previous, current in zip(values[-period-1:-1], values[-period:]):
            change = current - previous
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def atr(candles, period=14):
        if len(candles) < period + 1:
            return 0.0
        ranges = []
        selected = candles[-period-1:]
        for previous, current in zip(selected[:-1], selected[1:]):
            ranges.append(max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            ))
        return sum(ranges) / len(ranges)

    def analyze(self, symbol):
        candles = list(self.candles_1m[symbol])
        if len(candles) < 25:
            return None

        closes = [x["close"] for x in candles]
        volumes = [x["volume"] for x in candles]
        price = closes[-1]
        ema9 = self.ema(closes[-25:], 9)
        ema21 = self.ema(closes[-25:], 21)
        rsi = self.rsi(closes, 14)
        atr = self.atr(candles, 14)

        if price <= 0 or atr <= 0:
            return None

        average_volume = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0
        volume_ratio = volumes[-1] / average_volume if average_volume > 0 else 0
        momentum = ((closes[-1] / closes[-4]) - 1) * 100
        candle_change = ((candles[-1]["close"] / candles[-1]["open"]) - 1) * 100

        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []

        if ema9 > ema21:
            long_score += 22
            long_reasons.append("EMA9 أعلى EMA21")
        else:
            short_score += 22
            short_reasons.append("EMA9 أسفل EMA21")

        if price > ema9:
            long_score += 13
            long_reasons.append("السعر أعلى EMA9")
        else:
            short_score += 13
            short_reasons.append("السعر أسفل EMA9")

        if 52 <= rsi <= 70:
            long_score += 16
            long_reasons.append(f"RSI داعم {rsi:.1f}")
        elif 30 <= rsi <= 48:
            short_score += 16
            short_reasons.append(f"RSI داعم {rsi:.1f}")

        if momentum >= 0.18:
            long_score += 16
            long_reasons.append("زخم 3 دقائق صاعد")
        elif momentum <= -0.18:
            short_score += 16
            short_reasons.append("زخم 3 دقائق هابط")

        if volume_ratio >= 1.4:
            if candle_change > 0:
                long_score += 18
                long_reasons.append(f"فوليوم شراء ×{volume_ratio:.1f}")
            elif candle_change < 0:
                short_score += 18
                short_reasons.append(f"فوليوم بيع ×{volume_ratio:.1f}")

        # اتجاه 5 دقائق يصبح فلترًا إضافيًا بعد اكتمال 10 شموع.
        trend_candles = list(self.candles_5m[symbol])
        if len(trend_candles) >= 10:
            trend_closes = [x["close"] for x in trend_candles]
            trend_ema5 = self.ema(trend_closes, 5)
            trend_ema9 = self.ema(trend_closes, 9)
            if trend_ema5 > trend_ema9:
                long_score += 12
                long_reasons.append("اتجاه 5 دقائق صاعد")
            else:
                short_score += 12
                short_reasons.append("اتجاه 5 دقائق هابط")

        funding = self.funding.get(symbol, 0.0)
        if funding > 0.05:
            short_score += 3
            short_reasons.append("Funding موجب ومرتفع")
        elif funding < -0.05:
            long_score += 3
            long_reasons.append("Funding سالب ومرتفع")

        side = "LONG" if long_score > short_score else "SHORT"
        score = max(long_score, short_score)
        reasons = long_reasons if side == "LONG" else short_reasons

        if score < self.min_score:
            return None

        # لا نطارد شمعة بعيدة جدًا عن EMA9.
        extension = abs(price - ema9) / price * 100
        if extension > max(0.8, atr / price * 100 * 2.2):
            score -= 10
            reasons.append("السعر ممتد؛ انتظار إعادة اختبار أفضل")

        if score < self.min_score:
            return None

        if side == "LONG":
            stop = price - atr * 1.15
            target1 = price + atr * 1.0
            target2 = price + atr * 1.8
        else:
            stop = price + atr * 1.15
            target1 = price - atr * 1.0
            target2 = price - atr * 1.8

        return {
            "symbol": symbol.upper(),
            "side": side,
            "score": int(min(score, 100)),
            "entry": round(price, 8),
            "stop": round(stop, 8),
            "target1": round(target1, 8),
            "target2": round(target2, 8),
            "rsi": round(rsi, 2),
            "volume_ratio": round(volume_ratio, 2),
            "funding": round(funding, 4),
            "reasons": reasons[:6],
            "created_at": self.now_iso(),
        }

    def _save_and_notify(self, signal):
        key = f"{signal['symbol']}:{signal['side']}"
        now = datetime.now(timezone.utc)

        with self.lock:
            # لا نكرر نفس العملة والاتجاه خلال فترة التهدئة.
            previous = self.last_notified.get(key)
            if previous and now - previous < self.cooldown:
                return

            self.signals.appendleft(signal)
            self.last_notified[key] = now

        if self.notifier.enabled:
            self.notifier.send_signal(signal)

    def get_signals(self):
        with self.lock:
            return list(self.signals)

    def status(self):
        with self.lock:
            ready = sum(1 for s in self.symbols if len(self.candles_1m[s]) >= 25)
            return {
                "name": "Binance Futures WebSocket Live Scanner",
                "mode": "analysis-only",
                "websocket_connected": self.websocket_connected,
                "last_message_at": self.last_message_at,
                "last_error": self.last_error,
                "message_count": self.message_count,
                "reconnect_count": self.reconnect_count,
                "symbol_count": len(self.symbols),
                "ready_symbols": ready,
                "telegram_connected": self.notifier.enabled,
                "warmup": "يبدأ التحليل بعد 25 شمعة دقيقة لكل عملة",
                "dashboard": "/dashboard",
                "signals": "/signals",
            }
