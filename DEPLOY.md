# Запуск без Docker

Альтернатива `docker compose up` — запустить бота как обычный Python-процесс
прямо на сервере. Не требует Docker и не занимает его ресурсы. Два варианта:
разовый запуск в терминале и запуск как systemd-сервис (может работать сколь
угодно долго, пока его не остановишь).

## Общая подготовка

Нужен [uv](https://docs.astral.sh/uv/). Версии зависимостей зафиксированы в
`uv.lock`, окружение создаётся в `.venv`:

```bash
cd <путь-к-репозиторию>
uv sync --frozen --no-dev
```

`--frozen` ставит ровно то, что записано в `uv.lock`, и не пересчитывает его.
Версия Python берётся из `.python-version` (3.14, как в Dockerfile): uv сам
скачает интерпретатор в `~/.local/share/uv/python`, системный Python не нужен.
Этот интерпретатор нельзя удалять, пока на нём стоит `.venv`.
Без uv (нужен системный Python 3.14 или совместимый): `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
(`requirements.txt` генерируется из `uv.lock`, им же пользуется Dockerfile).

`TgCrypto` собирается из исходников — если сборка падает, поставьте
компилятор (`build-essential`). С интерпретатором от uv заголовки Python уже
в комплекте; `python3-dev` нужен только при системном Python без uv:

```bash
sudo apt install -y build-essential
```

`.env` держите для запуска через Docker (там `STORAGE_DIR=/storage` — путь
внутри контейнера, подставляется томом). Для запуска без Docker сделайте
отдельную копию `.env.local`:

```bash
cp .env .env.local
```

и укажите в ней `STORAGE_DIR` на реальный путь на диске (без Docker он не
подставляется томом) — либо абсолютным путём:

```
STORAGE_DIR=<путь-к-репозиторию>/storage
```

либо путём без начального слэша — тогда файлы будут сохраняться в
`./storage` относительно текущей рабочей директории процесса (для варианта 1
это будет корень репозитория, для systemd — `WorkingDirectory` из юнита):

```
STORAGE_DIR=storage
```

## Вариант 1 — разовый запуск

```bash
cd <путь-к-репозиторию>
uv run --frozen --no-dev --env-file .env.local bot.py
```

Останавливается `Ctrl+C`. Ничего не остаётся в фоне после закрытия терминала
(если не запущено внутри `tmux`/`screen`).

Если всё же запускали внутри `tmux` и закрыли терминал не остановив бота —
процесс продолжает работать в сессии `tmux`. Чтобы остановить:

```bash
tmux list-sessions          # найти имя/номер сессии
tmux attach -t <имя>        # зайти обратно и нажать Ctrl+C
# или сразу, не заходя внутрь:
tmux kill-session -t <имя>
```

Активировать venv не нужно: `uv run` сам берёт `.venv` из корня репозитория.

## Вариант 2 — systemd-сервис

Даёт: запуск/остановка одной командой, автоперезапуск при падении, опционально
автозапуск при загрузке сервера. Пока сервис не остановлен явно — работает
бессрочно.

Создайте `/etc/systemd/system/mediasaver-bot.service`:

```ini
[Unit]
Description=Telegram Media Saver Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<linux-пользователь>
WorkingDirectory=<путь-к-репозиторию>
EnvironmentFile=<путь-к-репозиторию>/.env.local
ExecStart=<путь-к-репозиторию>/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Юнит запускает Python из `.venv` напрямую, без `uv run`: при рестарте не нужны
сеть и кэш uv, версии не меняются. Обновлять окружение — `uv sync --frozen
--no-dev` при деплое, затем `systemctl restart`.

Применить и запустить:

```bash
sudo systemctl daemon-reload
sudo systemctl start mediasaver-bot     # запустить сейчас
sudo systemctl status mediasaver-bot    # проверить статус
journalctl -u mediasaver-bot -f         # смотреть логи в реальном времени
```

Остановить, когда бот больше не нужен:

```bash
sudo systemctl stop mediasaver-bot
```

Автозапуск при перезагрузке сервера (необязательно — включайте, только если
хотите, чтобы бот сам поднимался после ребута):

```bash
sudo systemctl enable mediasaver-bot    # включить автозапуск
sudo systemctl disable mediasaver-bot   # выключить автозапуск
```

Если автозапуск не включён (`enable` не вызывался) — сервис ведёт себя как
обычный переключатель: `start`/`stop` вручную, когда нужно, без разовой
настройки заново.
