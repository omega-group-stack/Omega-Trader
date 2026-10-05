<div dir="rtl">

# راهنمای استقرار Omega-Trader

این سند جواب یک سؤال مشخص است: **ربات را کجا اجرا کنم؟**

خلاصه‌ی یک‌خطی: برای معامله‌ی واقعی به یک **VPS ویندوزی** نیاز دارید. هاست اشتراکی
(cPanel / DirectAdmin) به‌هیچ‌وجه کار نمی‌کند. برای بک‌تست و کاغذی، لپ‌تاپ خودتان کافی است.

</div>

---

<div dir="rtl">

## ۱. چرا اصلاً سرور؟ جدول تصمیم

| کاری که می‌خواهید بکنید | کجا اجرا شود | چرا |
|---|---|---|
| `omega backtest` / `omega walkforward` | لپ‌تاپ خودتان، لینوکس یا ویندوز | روی داده‌ی ذخیره‌شده کار می‌کند؛ نه اینترنت دائم می‌خواهد نه بروکر |
| `omega paper` با فید CSV | لپ‌تاپ یا هر VPS لینوکسی | بروکر شبیه‌سازی‌شده است |
| `omega paper` با فید **MT5** | **VPS ویندوزی** | کتابخانه‌ی `MetaTrader5` فقط ویندوزی است |
| `omega live` (پول واقعی) | **VPS ویندوزی، ۲۴/۵ روشن** | هم محدودیت ویندوز، هم نیاز به آپ‌تایم |

مرز اصلی اینجاست: **هر چیزی که به ترمینال MetaTrader 5 وصل می‌شود، ویندوز می‌خواهد.**

</div>

---

<div dir="rtl">

## ۲. محدودیت ویندوز — چرا راه فرار ندارد

پایتون با MT5 از طریق پکیج رسمی `MetaTrader5` حرف می‌زند. آن پکیج یک wrapper نازک روی
API ترمینال دسکتاپ MT5 است و با مکانیزم IPC ویندوز (named pipe) به ترمینالِ **در حال
اجرا** وصل می‌شود. یعنی:

- پکیج فقط چرخ (wheel) ویندوزی دارد. روی لینوکس `pip install MetaTrader5` شکست می‌خورد.
- حتی اگر نصب می‌شد، بی‌فایده بود: باید یک `terminal64.exe` واقعاً باز و لاگین‌شده باشد
  تا وصل شود. API مستقل از ترمینال وجود ندارد.
- بنابراین «سرور» شما در عمل یعنی: یک ویندوز که ترمینال MT5 رویش دائماً باز است،
  به حساب بروکر لاگین کرده، و اسکریپت پایتون کنارش اجرا می‌شود.

در همین مخزن این محدودیت را با import تنبل مدیریت کرده‌ایم
(`omega/data/mt5_feed.py::mt5_module()` و داخل `build_broker`)، تا کل پروژه روی
لینوکس نصب و تست شود و فقط لحظه‌ی اتصال واقعی به بروکر، نبودِ ویندوز را گزارش کند.

### راه‌های جایگزین (و چرا توصیه نمی‌شوند)

| راه | وضعیت |
|---|---|
| Wine / Docker + Wine روی لینوکس | گاهی بالا می‌آید، ولی بی‌ثبات است. برای چیزی که پول شما را جابه‌جا می‌کند، «گاهی کار می‌کند» یعنی «کار نمی‌کند» |
| ماشین مجازی ویندوز روی سرور لینوکس | کار می‌کند ولی عملاً همان VPS ویندوزی است با سربار بیشتر |
| نوشتن Expert Advisor به زبان MQL5 | کار می‌کند و به پایتون نیاز ندارد — ولی یعنی بازنویسی کامل پروژه |
| **VPS ویندوزی** | **راه استاندارد و توصیه‌شده** |

</div>

---

<div dir="rtl">

## ۳. چرا هاست اشتراکی (cPanel) جواب نمی‌دهد

این سؤال را خیلی‌ها می‌پرسند، پس صریح: هاست اشتراکی برای این کار ساخته نشده است.

- **لینوکس است** → همان مشکل بند ۲.
- **پروسه‌ی دائمی نمی‌دهد.** مدل هاست اشتراکی request/response است؛ پروسه‌ی شما بعد از
  چند دقیقه kill می‌شود. ربات معاملاتی باید هفته‌ها زنده بماند.
- **اتصال خروجی محدود است.** معمولاً فقط HTTP/HTTPS باز است، نه پورت‌های بروکر.
- **کنترل روی نصب ندارید.** نه ترمینال MT5 نصب می‌کنید، نه سرویس تعریف می‌کنید.

اگر فقط می‌خواهید **داشبورد** را جایی میزبانی کنید و موتور معاملاتی جای دیگری است،
باز هم هاست اشتراکی مناسب نیست چون داشبورد به همان پروسه‌ی زنده‌ی ربات وصل است.

</div>

---

<div dir="rtl">

## ۴. مشخصات VPS

ربات سبک است. گلوگاه، ترمینال MT5 است نه پایتون.

| | حداقل | توصیه‌شده |
|---|---|---|
| CPU | ۲ هسته | ۴ هسته |
| RAM | ۴ گیگابایت | ۸ گیگابایت |
| دیسک | ۴۰ گیگ SSD | ۸۰ گیگ SSD |
| سیستم‌عامل | Windows Server 2019 | Windows Server 2022 |

نکته‌ی واقعی: ۴ گیگ وقتی MT5 + مرورگر + Python هم‌زمان باز باشند، تنگ می‌شود.
اگر چند نماد را با تایم‌فریم پایین اجرا می‌کنید، ۸ گیگ بگیرید.

### تأخیر (latency) — مهم ولی نه آن‌قدر که می‌گویند

VPS را **نزدیک به سرور بروکرتان** بگیرید، نه نزدیک به خودتان. اکثر بروکرهای فارکس
سرورهایشان در **لندن (LD4)** یا **نیویورک (NY4)** است. از بروکر بپرسید کجاست.

ولی انتظار معجزه نداشته باشید: استراتژی این پروژه روی کندل‌های **M15 یا H1** تصمیم
می‌گیرد. فرق ۵ میلی‌ثانیه و ۸۰ میلی‌ثانیه برای چنین استراتژی‌ای عملاً صفر است.
تأخیر وقتی حیاتی می‌شود که اسکالپ یا آربیتراژ کنید. برای ما مهم‌تر از تأخیر،
**پایداری اتصال** است — یک قطعی سه‌دقیقه‌ای خیلی بدتر از ۵۰ میلی‌ثانیه تأخیر است.

بسیاری از بروکرها VPS رایگان یا یارانه‌ای می‌دهند اگر حجم معاملات یا موجودی‌تان از حدی
بیشتر باشد. قبل از خرید بپرسید.

</div>

---

<div dir="rtl">

## ۵. نصب روی VPS ویندوزی

</div>

```powershell
# 1) Python 3.11+  (tick "Add python.exe to PATH" in the installer)
python --version

# 2) MetaTrader 5 terminal: install, log in to your account,
#    then Tools -> Options -> Expert Advisors -> enable "Allow algorithmic trading".

# 3) Get the bot
git clone https://github.com/omega-group-stack/Omega-Trader.git
cd Omega-Trader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install MetaTrader5          # Windows only — this is the step Linux cannot do

# 4) Starter config
python -m omega.cli init -p config\live.yaml
```

<div dir="rtl">

سپس `config\live.yaml` را ویرایش کنید:

</div>

```yaml
execution:
  mode: live
  confirm_live: true          # safety interlock — live mode refuses to start without it

mt5:
  login: ${MT5_LOGIN}         # never hard-code these in the file
  password: ${MT5_PASSWORD}
  server: "YourBroker-Live"
  terminal_path: "C:/Program Files/MetaTrader 5/terminal64.exe"

data:
  source: mt5

risk:
  risk_per_trade_pct: 0.5     # start lower than you think you should
  max_drawdown_pct: 10.0

dashboard:
  host: 127.0.0.1             # see section 7 before changing this
  auth_token: ${OMEGA_DASH_TOKEN}

notifications:
  enabled: true
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    chat_id: ${TELEGRAM_CHAT_ID}
```

<div dir="rtl">

رمزها را در متغیرهای محیطی ویندوز بگذارید، نه در فایل:

</div>

```powershell
[Environment]::SetEnvironmentVariable("MT5_LOGIN", "12345678", "Machine")
[Environment]::SetEnvironmentVariable("MT5_PASSWORD", "...",    "Machine")
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "...", "Machine")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "...", "Machine")
```

<div dir="rtl">

**قبل از پول واقعی، حداقل دو هفته کاغذی اجرا کنید:**

</div>

```powershell
python -m omega.cli paper -c config\live.yaml --set data.source=mt5
```

---

<div dir="rtl">

## ۶. ۲۴/۵ نگه‌داشتن ربات

بازار فارکس از یکشنبه ۲۲:۰۰ UTC تا جمعه ۲۲:۰۰ UTC باز است. در این بازه ربات باید زنده
باشد و **اگر مرد، خودش برگردد**. سه راه، از بهترین به ساده‌ترین:

### الف) NSSM — سرویس واقعی ویندوز (توصیه‌شده)

NSSM اسکریپت را به یک سرویس ویندوز تبدیل می‌کند: با بوت بالا می‌آید، بدون نیاز به
لاگین کاربر اجرا می‌شود، و خودکار ری‌استارت می‌کند.

</div>

```powershell
# Download nssm from https://nssm.cc  then:
nssm install OmegaTrader "C:\Omega-Trader\.venv\Scripts\python.exe"
nssm set OmegaTrader AppParameters  "-m omega.cli live -c config\live.yaml"
nssm set OmegaTrader AppDirectory   "C:\Omega-Trader"
nssm set OmegaTrader Start          SERVICE_AUTO_START

# restart on crash, with backoff, and keep logs
nssm set OmegaTrader AppExit Default Restart
nssm set OmegaTrader AppRestartDelay 15000
nssm set OmegaTrader AppStdout "C:\Omega-Trader\runtime\service.out.log"
nssm set OmegaTrader AppStderr "C:\Omega-Trader\runtime\service.err.log"
nssm set OmegaTrader AppRotateFiles 1

nssm start OmegaTrader
nssm status OmegaTrader
```

<div dir="rtl">

مهم: MT5 خودش هم باید همیشه باز باشد. یا آن را هم سرویس کنید، یا در Task Scheduler با
تریگر «at log on» اجرایش کنید و VPS را روی auto-login بگذارید.

### ب) Task Scheduler — بدون نصب ابزار اضافه

</div>

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Omega-Trader\.venv\Scripts\python.exe" `
           -Argument "-m omega.cli live -c config\live.yaml" `
           -WorkingDirectory "C:\Omega-Trader"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit 0 -MultipleInstances IgnoreNew `
            -DontStopOnIdleEnd -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName "OmegaTrader" -Action $action `
    -Trigger $trigger -Settings $settings -RunLevel Highest
```

<div dir="rtl">

`-ExecutionTimeLimit 0` حتماً لازم است، وگرنه ویندوز بعد از ۷۲ ساعت پروسه را می‌کشد.

### ج) systemd — فقط برای حالت کاغذی/بک‌تست روی لینوکس

چون MT5 ویندوزی است، این فقط برای فید CSV یا سینتتیک معنا دارد — مثلاً یک داشبورد
نمایشی یا بک‌تست شبانه.

</div>

```ini
# /etc/systemd/system/omega-trader.service
[Unit]
Description=Omega-Trader (paper mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=omega
WorkingDirectory=/opt/Omega-Trader
EnvironmentFile=/etc/omega-trader.env
ExecStart=/opt/Omega-Trader/.venv/bin/python -m omega.cli paper -c config/paper.yaml
Restart=always
RestartSec=15
StartLimitIntervalSec=0

# basic hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/Omega-Trader/runtime

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omega-trader
journalctl -u omega-trader -f
```

<div dir="rtl">

### چیزهایی که واقعاً ربات را می‌کشند

| خطر | راه‌حل |
|---|---|
| ری‌استارت خودکار ویندوز بعد از آپدیت | ساعت فعال را به آخر هفته ببرید: `sconfig` → گزینه ۵ |
| قطع اتصال بروکر | `execution.poll_seconds` کوچک باشد؛ ربات خطای فید را لاگ می‌کند و ادامه می‌دهد |
| پر شدن دیسک از لاگ | چرخش لاگ (`AppRotateFiles 1` در NSSM) |
| قطعی برق/شبکه‌ی دیتاسنتر | پوزیشن‌های باز **همیشه** SL سمت بروکر دارند — اگر ربات بمیرد، استاپ‌ها سر جایشان‌اند |
| ری‌استارت وسط معامله | ربات پوزیشن‌های موجود بروکر را موقع `setup()` می‌خواند؛ یتیم نمی‌مانند |

نکته‌ی مهم: این ربات استاپ‌لاس را **روی سرور بروکر** می‌گذارد، نه در حافظه‌ی خودش.
این عمدی است. اگر VPS شما وسط شب بسوزد، پوزیشن‌هایتان همچنان محافظت‌شده‌اند.

</div>

---

<div dir="rtl">

## ۷. امنیت داشبورد — این را جدی بگیرید

داشبورد یک endpoint به نام `POST /api/control` دارد که می‌تواند **همه‌ی پوزیشن‌ها را
ببندد، درصد ریسک را عوض کند و ربات را متوقف کند**. تا نسخه‌ی قبل، هیچ احراز هویتی
نداشت. حالا دارد، ولی **پیش‌فرض خاموش است** — چون روی `127.0.0.1` مشکلی نیست.

**قانون: اگر `dashboard.host` چیزی جز `127.0.0.1` است، حتماً توکن بگذارید.**
اگر این کار را نکنید، ربات موقع بالا آمدن با حروف درشت هشدار می‌دهد.

</div>

```powershell
# generate a token
python -c "import secrets; print(secrets.token_hex(24))"

python -m omega.cli dashboard -c config\live.yaml --set dashboard.auth_token=<TOKEN>
```

<div dir="rtl">

بعد یک‌بار `http://<ip>:8080/?token=<TOKEN>` را باز کنید؛ توکن در کوکی می‌نشیند.
کلاینت‌های API می‌توانند هدر `X-Omega-Token` یا `Authorization: Bearer <TOKEN>` بفرستند.

**امن‌تر از توکن: اصلاً پورت را باز نکنید.** داشبورد را روی `127.0.0.1` نگه دارید و از
تونل SSH استفاده کنید:

</div>

```bash
ssh -L 8080:127.0.0.1:8080 user@your-vps
# now open http://localhost:8080 on your own machine
```

<div dir="rtl">

جدول تصمیم:

| سناریو | کار درست |
|---|---|
| فقط خودتان، از یک کامپیوتر | `host: 127.0.0.1` + تونل SSH |
| چند نفر، داخل شبکه‌ی خودتان | `host: 0.0.0.0` + `auth_token` + فایروال محدود به IP |
| دسترسی از اینترنت | `auth_token` + **HTTPS** از طریق reverse proxy (Caddy/nginx) + فایروال |
| هیچ‌وقت | پورت ۸۰۸۰ باز روی اینترنت بدون توکن |

HTTP ساده یعنی توکن به‌صورت متن ساده روی شبکه می‌رود. اگر از بیرون وصل می‌شوید،
HTTPS اختیاری نیست.

</div>

---

<div dir="rtl">

## ۸. کنترل از راه دور با تلگرام

اگر داشبورد را اصلاً باز نکنید، تلگرام جایگزین کاملاً خوبی است: اتصال **خروجی** است،
پس هیچ پورتی روی VPS باز نمی‌شود.

</div>

```yaml
notifications:
  enabled: true
  provider: telegram
  on_entry: true
  on_exit: true
  on_halt: true
  on_error: true
  daily_summary_hour: 21        # UTC
  heartbeat_minutes: 0          # set to e.g. 240 for a silent "still alive" ping
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    chat_id:   ${TELEGRAM_CHAT_ID}
    allow_commands: true
    allow_flatten: true         # set false if you only want read-only control
```

```powershell
python -m omega.cli notify test      # verify before you rely on it
```

<div dir="rtl">

دستورهای موجود: `/status`، `/positions`، `/signal`، `/trades`، `/risk 0.5`،
`/halt`، `/resume`، `/flatten`، `/stop`.

**امنیت:** ربات فقط به `chat_id` تنظیم‌شده پاسخ می‌دهد و پیام هر چت دیگری را لاگ و دور
می‌اندازد. توکن ربات را مثل رمز حساب بروکر نگه دارید — هر کسی که آن را داشته باشد،
می‌تواند پیام بفرستد. اگر فقط اطلاع‌رسانی می‌خواهید و نه کنترل، `allow_commands: false`.

</div>

---

<div dir="rtl">

## ۹. داکر — فقط برای بک‌تست و کاغذی

تکرار می‌کنم: داکر **حالت live را حل نمی‌کند**، چون MT5 ویندوزی است. ولی برای اجرای
بازتولیدپذیر بک‌تست و walk-forward عالی است.

</div>

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m omega && chown -R omega:omega /app
USER omega
EXPOSE 8080
CMD ["python", "-m", "omega.cli", "dashboard", \
     "--set", "dashboard.host=0.0.0.0", \
     "--set", "data.source=csv", "--set", "data.csv_dir=data"]
```

```bash
docker build -t omega-trader .
docker run --rm -v "$PWD/data:/app/data:ro" -v "$PWD/runtime:/app/runtime" \
  omega-trader python -m omega.cli backtest \
  --set data.source=csv --set data.history_bars=200000 -s EURUSD
```

---

<div dir="rtl">

## ۱۰. چک‌لیست قبل از اولین معامله‌ی واقعی

- [ ] `omega walkforward` روی داده‌ی واقعی اجرا شده و خروجی **خارج از نمونه** را دیده‌ام
- [ ] حداقل دو هفته `omega paper` روی همان VPS، بدون کرش
- [ ] `execution.confirm_live: true` آگاهانه ست شده
- [ ] `risk.risk_per_trade_pct` روی ۰.۲۵ تا ۰.۵ (نه ۱ و نه بیشتر) برای شروع
- [ ] `risk.max_drawdown_pct` ست شده و می‌دانم کیل‌سوییچ چه‌کار می‌کند
- [ ] حساب **سنت یا دمو** اول — نه حساب اصلی
- [ ] سرویس ری‌استارت خودکار تست شده (پروسه را دستی بکشید و ببینید برمی‌گردد)
- [ ] داشبورد یا توکن دارد یا پشت `127.0.0.1` است
- [ ] تلگرام تست شده و `/status` جواب می‌دهد
- [ ] مشخصات بروکر (spread, commission, swap) در کانفیگ با واقعیت حساب یکی است
- [ ] می‌دانم چطور با `/flatten` یا دکمه‌ی داشبورد همه‌چیز را فوراً ببندم

آخرین حرف: نتایج بک‌تست در `BACKTEST.md` را قبل از ریسک کردن پول بخوانید.
آن سند صادقانه می‌گوید این استراتژی با تنظیمات پیش‌فرض روی داده‌ی واقعی **ضرر می‌دهد**.

</div>
