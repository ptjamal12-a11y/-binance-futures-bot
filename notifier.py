import requests


class TelegramNotifier:
    def __init__(self, token="", chat_id=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def send_signal(self, s):
        if not self.enabled:
            return False

        direction = "🟢 شراء LONG" if s["side"] == "LONG" else "🔴 بيع SHORT"
        reasons = "\n".join(f"• {x}" for x in s["reasons"])

        text = (
            f"⚡ فرصة سكالبينغ\n\n"
            f"{direction}\n"
            f"العملة: {s['symbol']}\n"
            f"الثقة: {s['score']}/100\n"
            f"الفريم: {s['interval']} | اتجاه: {s['trend_interval']}\n\n"
            f"دخول مرجعي: {s['entry_reference']}\n"
            f"وقف: {s['stop_loss']}\n"
            f"هدف 1: {s['target_1']}\n"
            f"هدف 2: {s['target_2']}\n\n"
            f"RSI: {s['rsi']}\n"
            f"الفوليوم: ×{s['volume_ratio']}\n"
            f"Funding: {s['funding_rate_percent']}%\n"
            f"تغير OI: {s['open_interest_change_percent']}%\n\n"
            f"{reasons}\n\n"
            "⚠️ تحليل آلي وليس ضمانًا. استخدم وقف خسارة ولا تطارد السعر."
        )

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
            timeout=12,
        )
        return response.ok
