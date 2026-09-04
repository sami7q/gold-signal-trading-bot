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

**كيف يعمل النظام على السيرفر فعليًا** (وليس عبر NSSM كما في الجزء ب أعلاه — ذاك الجزء يصف
الخطة الأصلية، والتنفيذ الفعلي اختلف):

| المكوّن | يشغّله | يخص |
|---|---|---|
| الهَب: uvicorn + تيليكرام + تنفيذ | مهمة مجدولة `GoldBotBackend` | الحساب في `C:\gold-bot\.env` |
| عامل لكل حساب إضافي | مهمة مجدولة `GoldBotWorker-<رقم الحساب>` | ملف `.env` خاص عبر `GOLDBOT_ENV_FILE` |
| نفق Cloudflare (Vercel ← الباك-إند) | خدمة `GoldBotTunnel` | لا علاقة له بالحسابات — لا تلمسه |

الترتيب أدناه يقصر التوقف على إعادة تشغيل واحدة، ولا يمسّ أي حساب آخر (العامل يبقى شغّالًا):

1. **الكود والمخطط:** `git pull` ثم `cd C:\gold-bot\backend` و `.venv\Scripts\alembic upgrade head`.
2. **حدّث `.env` أولًا** (الخطوة 5 أدناه)، ثم **سجّل الحساب من نفس قيمه** — هكذا يستحيل أن
   تختلف هوية العملية عن الصف المسجَّل:
   `.venv\Scripts\python scripts\register_account.py sami "الاسم المعروض" --shared`
   (`--shared` يجعله يظهر لكل المشغّلين). البديل: أضفه يدويًا من اللوحة (صفحة "حسابات MT5" ←
   "حساب جديد") مع مفتاح **"حساب مشترك بين المشغّلين"** — لكن انتبه أن المطابقة حرفية
   وحسّاسة لحالة الأحرف. في الحالتين تُخزَّن كلمة المرور مشفّرة بمفتاح
   `ACCOUNT_CREDENTIALS_ENCRYPTION_KEY`، ولا تُكتب في أي ملف هجرة.
3. **انقل الاستراتيجية:** `.venv\Scripts\python scripts\copy_settings.py <القديم> <الجديد>` — بدونها
   يبدأ الحساب الجديد بالقيم الافتراضية لا بإعداداتك المضبوطة. عيّن الحساب بـ
   `<login>@<server>` لو كان الرقم مسجّلًا على أكثر من خادم.
4. **بدّل المحطة:** ثبّت محطة MT5 الخاصة بالوسيط الجديد وسجّل دخول الحساب فيها.
5. **حدّث `.env`:** `MT5_ACCOUNT` و`MT5_PASSWORD` و`MT5_SERVER`، و**`MT5_TERMINAL_PATH` إلزامي**
   عند وجود أكثر من محطة MT5 على الجهاز. هذه القيم هي ما تُعرّف العملية بنفسها به: تُطابَق حرفيًا
   (وبحساسية لحالة الأحرف) مع `mt5_login`/`mt5_server` في الجدول
   (`AccountService.resolve_own_process_account`)، فلا بد أن تطابق الصف المُضاف في الخطوة 2.
6. **أعد تشغيل الهَب وحده:** `Stop-ScheduledTask -TaskName GoldBotBackend` ثم
   `Start-ScheduledTask -TaskName GoldBotBackend`.
7. **تحقّق أن الاتصال حقيقي — لا تكتفِ بشارة "متصل الآن":**
   `.venv\Scripts\python scripts\check_accounts.py` يطبع الصورة كاملة (من يرى أي حساب،
   أي صف تطابقه هذه العملية، هل النبض حديث، وهل الوسيط MT5 أم ورقي) وينتهي بحكم
   "جاهز / غير جاهز بعد". لا يكتب في قاعدة البيانات شيئًا. أو يدويًا:
   `Invoke-RestMethod http://localhost:8000/api/health/broker` لازم يرجع `broker = mt5`. لو رجع
   `paper` فالاتصال بالمحطة فشل وسقط البوت تلقائيًا على الوسيط الورقي (`app/lifecycle.py`) —
   وهو ما زال يكتب نبضًا ورصيدًا وهميًا، فتظهر الشارة خضراء وهي كاذبة.
8. **احذف الحساب القديم** بعد ~60 ثانية من الخطوة 6 (حتى يصير آخر نبض له قديمًا، وإلا يرفض
   السكربت): `.venv\Scripts\python scripts\delete_account.py <رقم الحساب> [الخادم]` — حذف نهائي
   للصف مع كل صفقاته وإعداداته. للإخفاء القابل للتراجع بدلًا من ذلك، استخدم "أرشفة" من اللوحة.

---

## مراقبة أساسية
`app/lifecycle.py` أصلاً يشغّل `_poll_open_trades` كل `POSITION_POLL_SECONDS` (افتراضي 10-20 ثانية) — يكتشف إغلاق الصفقات تلقائيًا حتى لو صارت خارج البوت. لتنبيه بسيط إضافي، سجّل Task مجدول (Task Scheduler) يستدعي `GET /api/health` كل 5 دقائق ويرسل بريد/تنبيه لو رجع غير 200 — بدون أدوات مراقبة خارجية معقّدة بهالمرحلة.
