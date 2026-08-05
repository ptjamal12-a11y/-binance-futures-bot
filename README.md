# Binance Futures WebSocket Live Scanner

هذه النسخة لا تستخدم Binance REST API المحظور برمز 451 على بعض الاستضافات.

## الاتصال المستخدم

- `wss://fstream.binance.com/market/stream`
- شموع 1 دقيقة و5 دقائق
- Mark Price وFunding كل ثانية
- 20 عقد USDT رئيسيًا

## ما يظهر فورًا؟

افتح `/dashboard`. إذا ظهر **متصل** وبدأ عداد الرسائل يرتفع، فإن Railway متصل ببث Binance المباشر.

## متى يبدأ التحليل؟

يحتاج 25 شمعة دقيقة تقريبًا بعد التشغيل لأول مرة، لأن النسخة لا تستخدم REST لجلب التاريخ. بعد ذلك يحلل عند إغلاق كل شمعة دقيقة ويرسل الإشارة فورًا.

## الروابط

- `/dashboard`
- `/health`
- `/signals`
- `/restart`

## مهم

لا يفتح صفقات ولا يحتاج Binance API Key.
