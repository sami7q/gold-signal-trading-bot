# دليل نشر البوت على سيرفر مخصص (VPS) — تشغيل 24 ساعة

**الوضع:** DEMO (حساب MT5 التجريبي `50071008`، سيرفر `Axi-US50-Demo`) — مو أموال حقيقية.
حساب مشترك: مسجَّل مرة واحدة في قاعدة البيانات بخانة "مشترك" مفعّلة، فيظهر بنفس الصلاحيات لتسجيلَي الدخول (سامي ومصطفى). الحساب التجريبي القديم `111715721` / `MetaQuotes-Demo` حُذف نهائيًا (2026-09-04) عبر `backend/scripts/delete_account.py`.
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
حمّل وثبّت محطة **MT5 الخاصة بـ Axi** (تثبيت مستقل عن أي محطة MT5 أخرى على نفس الجهاز)، وسجّل دخول بالحساب `50071008` / السيرفر `Axi-US50-Demo`. لو كان على الجهاز أكثر من محطة MT5، اضبط `MT5_TERMINAL_PATH` في `.env` على مسار `terminal64.exe` الخاص بـ Axi تحديدًا — وإلا فقد يتصل البوت بالمحطة الخطأ.

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

## تبديل حساب MT5 الذي يتداول عليه البوت (الإجراء المستخدم في 2026-09-04)

الترتيب مهم: صف الحساب في قاعدة البيانات، ثم إيقاف الخدمة، ثم المحطة و`.env`، ثم حذف القديم.

1. **حدّث الكود والمخطط:** `git pull` ثم `cd C:\gold-bot\backend` و `.venv\Scripts\alembic upgrade head`
   (يضيف عمود `is_shared` لجدول `mt5_accounts`).
2. **أضف الحساب الجديد من اللوحة** (صفحة "حسابات MT5" ← "حساب جديد") بالبيانات الحقيقية،
   وفعّل مفتاح **"حساب مشترك بين المشغّلين"** ليظهر لسامي ومصطفى معًا. لا تُكتب كلمة مرور MT5
   في ملف هجرة ولا في أي سكربت — اللوحة تشفّرها بمفتاح `ACCOUNT_CREDENTIALS_ENCRYPTION_KEY`.
3. **أوقف الخدمة:** `nssm stop GoldBot` (وكل عملية `worker.py` إن وُجدت).
4. **بدّل محطة MT5:** ثبّت/شغّل محطة الوسيط الجديد وسجّل دخول الحساب الجديد فيها.
5. **حدّث `.env`:** `MT5_ACCOUNT` و `MT5_PASSWORD` و `MT5_SERVER` (و`MT5_TERMINAL_PATH` عند
   وجود أكثر من محطة). هذه القيم هي ما تُعرّف العملية بنفسها به: تُطابَق مع
   `mt5_login`/`mt5_server` في الجدول (`AccountService.resolve_own_process_account`)،
   فلا بد أن تطابق الصف المُضاف في الخطوة 2 حرفيًا.
6. **احذف الحساب القديم نهائيًا** (بعد إيقاف الخدمة، وإلا يرفض السكربت):
   `.venv\Scripts\python scripts\delete_account.py <رقم_الحساب_القديم>` — يحذف الصف مع كل
   صفقاته وإعداداته. للإخفاء القابل للتراجع بدلًا من الحذف، استخدم "أرشفة" من اللوحة.
7. **شغّل الخدمة وتحقّق:** `nssm start GoldBot` ثم افتح صفحة الحسابات — يجب أن يظهر الحساب
   الجديد بشارة "متصل الآن" خلال ~10 ثوانٍ، وبنفس الشكل عند تسجيل دخول المشغّل الآخر.

---

## مراقبة أساسية
`app/lifecycle.py` أصلاً يشغّل `_poll_open_trades` كل `POSITION_POLL_SECONDS` (افتراضي 10-20 ثانية) — يكتشف إغلاق الصفقات تلقائيًا حتى لو صارت خارج البوت. لتنبيه بسيط إضافي، سجّل Task مجدول (Task Scheduler) يستدعي `GET /api/health` كل 5 دقائق ويرسل بريد/تنبيه لو رجع غير 200 — بدون أدوات مراقبة خارجية معقّدة بهالمرحلة.
