# دليل نشر البوت على سيرفر مخصص (VPS) — تشغيل 24 ساعة

**الوضع:** DEMO (حساب MT5 التجريبي `111715721`، سيرفر `MetaQuotes-Demo`) — مو أموال حقيقية.
**لماذا Windows VPS تحديدًا:** محطة MT5 (MetaTrader5 Terminal) تطبيق سطح مكتب حصري لـ Windows — ما يشتغل على Linux/Docker مباشرة.

---

## ✅ الجزء أ: تعديلات الكود — منجزة ومُختبَرة

فلتر جودة الستوب (`max_channel_stop_pips`, افتراضي 200 نقطة) مُطبَّق بمحرك القواعد الحي (`app/trading/rules.py`)، قابل للتعديل من لوحة التحكم (صفحة الإعدادات → "أقصى مسافة لستوب القناة"، أفرغ الحقل لتعطيله). **235 اختبار + ruff + mypy نظيفة، والـ API جُرِّب حيًا (GET/PATCH/تعطيل/إعادة تفعيل) ونجح.**

---

## الجزء ب: خطوات النشر على VPS

### 1. اختيار السيرفر
أي مزوّد يوفّر **Windows Server** (لا يحتاج مواصفات ضخمة — بوت واحد بحمل خفيف):
- أمثلة شائعة: Contabo (أرخص)، Vultr، AWS Lightsail (Windows)، Azure VM.
- الحد الأدنى المعقول: 2 vCPU / 4GB RAM / Windows Server 2019+.

### 2. تجهيز السيرفر (بعد أول تسجيل دخول RDP)
```powershell
# تثبيت Python 3.12 (حمّل المثبت من python.org، أو عبر winget)
winget install Python.Python.3.12

# تثبيت Git
winget install Git.Git

# تثبيت NSSM (لتشغيل البوت كخدمة Windows حقيقية)
winget install nssm
```
حمّل وثبّت محطة **MT5** الخاصة بالوسيط (نفس النسخة المستخدمة محليًا)، سجّل دخول بنفس الحساب `111715721` / السيرفر `MetaQuotes-Demo`.

### 3. نقل الكود
```powershell
git clone <رابط_المستودع> C:\gold-bot
cd C:\gold-bot\backend
python -m venv .venv
.venv\Scripts\pip install -e ".[telegram,mt5]"
```

### 4. نقل ملف جلسة تيليجرام (بدون تسجيل دخول جديد!)
انسخ `telegram_session` من جهازك المحلي إلى نفس المسار على الـ VPS (عبر RDP copy-paste أو `scp`). **لا تُعِد تسجيل الدخول** — الجلسة الحالية صالحة وتفادي هذا يجنّبك أي مشاكل حظر مؤقت من تيليجرام.

### 5. ملف `.env`
انسخ `.env` المحلي، وعدّل فقط لو اختلفت مسارات:
```
TELEGRAM_SESSION_PATH=C:\gold-bot\backend\telegram_session
```
باقي القيم (MT5_ACCOUNT، MT5_SERVER، TRADING_MODE=DEMO، AUTO_TRADING=true...) تبقى كما هي.

### 6. قاعدة البيانات
```powershell
cd C:\gold-bot\backend
.venv\Scripts\alembic upgrade head
```

### 7. تشغيله كخدمة Windows حقيقية (يعيد التشغيل تلقائيًا، يشتغل عند الإقلاع)
```powershell
nssm install GoldBot "C:\gold-bot\backend\.venv\Scripts\uvicorn.exe" "app.main:app --host 0.0.0.0 --port 8000"
nssm set GoldBot AppDirectory "C:\gold-bot\backend"
nssm set GoldBot AppExit Default Restart
nssm start GoldBot
```
تحقّق: `Get-Service GoldBot` يجب يظهر `Running`.

### 8. الداشبورد (Vercel، منشور مسبقًا)
حدّث متغيّر البيئة على Vercel (`NEXT_PUBLIC_API_URL` أو المكافئ) ليشير لعنوان IP/دومين الـ VPS بدل `localhost`. وتأكّد `DASHBOARD_CORS_ORIGINS` بـ `.env` الباك-إند يتضمن دومين Vercel.

---

## الجزء ج: قائمة فحص قبل اعتباره "شغال فعليًا"

- [ ] `TRADING_MODE=DEMO` مؤكَّد بملف `.env` على الـ VPS.
- [ ] `max_channel_stop_pips=200` (أو القيمة اللي تفضّلها 170-250) مفعّل عبر لوحة التحكم.
- [ ] MT5 على الـ VPS متصل فعليًا (`GET /api/health` أو فحص لوحة التحكم).
- [ ] تيليجرام متصل ويستقبل رسائل القناة الحقيقية.
- [ ] خدمة `GoldBot` بوضع `Running`، وتُعيد التشغيل تلقائيًا لو أعدت تشغيل السيرفر (اختبرها فعليًا: `Restart-Computer` ثم تحقّق).
- [ ] مراقبة يدوية أول 24-48 ساعة (لوحة التحكم + سجلات) قبل تركه بدون إشراف بالكامل.

---

## مراقبة أساسية
`app/lifecycle.py` أصلاً يشغّل `_poll_open_trades` كل `POSITION_POLL_SECONDS` (افتراضي 10-20 ثانية) — يكتشف إغلاق الصفقات تلقائيًا حتى لو صارت خارج البوت. لتنبيه بسيط إضافي، سجّل Task مجدول (Task Scheduler) يستدعي `GET /api/health` كل 5 دقائق ويرسل بريد/تنبيه لو رجع غير 200 — بدون أدوات مراقبة خارجية معقّدة بهالمرحلة.
