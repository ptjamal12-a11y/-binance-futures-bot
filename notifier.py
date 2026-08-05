import requests


class TelegramNotifier:
    def __init__(self, token="", chat_id=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def send_signal(self, signal):
        direction = "🟢 شراء LONG" if signal["side"] == "LONG" else "🔴 بيع SHORT"
        reasons = "\n".join(f"• {x}" for x in signal["reasons"])
        text = (
            f"⚡ فرصة مباشرة من Binance Futures\n\n"
            f"{direction}\n"
            f"العملة: {signal['symbol']}\n"
            f"الثقة: {signal['score']}/100\n\n"
            f"الدخول المرجعي: {signal['entry']}\n"
            f"وقف الخسارة: {signal['stop']}\n"
            f"الهدف الأول: {signal['target1']}\n"
            f"الهدف الثاني: {signal['target2']}\n\n"
            f"RSI: {signal['rsi']}\n"
            f"الفوليوم: ×{signal['volume_ratio']}\n"
            f"Funding: {signal['funding']}%\n\n"
            f"{reasons}\n\n"
            "⚠️ تحليل آلي وليس ضمانًا ولا يفتح صفقة."
        )
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=15,
            )
            return response.ok
        except requests.RequestException:
            return False
