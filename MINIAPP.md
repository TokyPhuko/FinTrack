# Telegram Mini App

FinTrack открывается **внутри Telegram** как Mini App (тот же фронт, что в браузере).

## Аутентификация

Вместо cookie-сессии Mini App шлёт заголовок:

```
Authorization: tma <Telegram.WebApp.initData>
```

Сервер валидирует HMAC-SHA256 (`services/tma.py`):
- `secret = HMAC_SHA256(key="WebAppData", msg=bot_token)`
- `hash = HMAC_SHA256(key=secret, msg=data_check_string)`
- `auth_date` не старше 5 минут

После валидации ищется `User.telegram_id == user.id` из initData.
Тот же middleware работает и для обычных cookie-сессий веба.

## Как открыть (локально)

Mini App в Telegram **требует HTTPS**. Локально:

### Вариант A — Cloudflare Tunnel (рекомендую)
```bash
# один раз
cloudflared tunnel --url http://127.0.0.1:8000
# в логе появится https://xxxx.trycloudflare.com
```
В `.env`:
```
MINI_APP_URL=https://xxxx.trycloudflare.com
```
Перезапусти сервер → в боте `/start` → появится кнопка **📊 Открыть FinTrack**.

### Вариант B — ngrok
```bash
ngrok http 8000
```
Аналогично пропиши URL в `MINI_APP_URL`.

### Вариант C — без Telegram
Просто открой `http://127.0.0.1:8000` в браузере и залогинься паролем.
Всё то же, кроме кнопки в боте.

## Дизайн-требования (уже учтены)

- `Telegram.WebApp.ready()` на старте каждого JS-файла
- Тема через CSS-переменные `--tg-theme-*` (можно добавить позже)
- Safe-area инсеты на iPhone
- Mobile-first разметка

## Проверка HMAC (unit)

```bash
cd fintrack
.venv/bin/python -c "
import hmac,hashlib,json,time
from urllib.parse import urlencode
from app.services.tma import validate_init_data
token='YOUR_BOT_TOKEN'
user=json.dumps({'id':1,'first_name':'T'})
ad=str(int(time.time()))
d={'auth_date':ad,'query_id':'q','user':user}
dcs='\n'.join(f'{k}={d[k]}' for k in sorted(d))
secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
h=hmac.new(secret,dcs.encode(),hashlib.sha256).hexdigest()
print(validate_init_data(urlencode({**d,'hash':h}), token))
"
```
