import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_signal(self, signal: dict) -> bool:
        if not self.enabled:
            return False

        direction = "شراء LONG" if signal["side"] == "LONG" else "بيع SHORT"
        reasons = "\n".join(f"• {reason}" for reason in signal["reasons"])

        text = (
            f"📊 {signal['symbol']} — {direction}\n"
            f"الثقة: {signal['score']}/100\n"
            f"الفريم: {signal['interval']}\n\n"
            f"دخول مرجعي: {signal['entry_reference']}\n"
            f"وقف الخسارة: {signal['stop_loss']}\n"
            f"الهدف الأول: {signal['target_1']}\n"
            f"الهدف الثاني: {signal['target_2']}\n\n"
            f"RSI: {signal['rsi']}\n"
            f"قوة الفوليوم: ×{signal['volume_ratio']}\n\n"
            f"{reasons}\n\n"
            "⚠️ الإشارة تحليلية وليست ضمانًا. لا تستخدم رافعة عالية قبل التجربة."
        )

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return response.ok
