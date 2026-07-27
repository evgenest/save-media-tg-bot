# Запуск без Docker

Альтернатива `docker compose up` — запустить бота как обычный Python-процесс
прямо на сервере. Не требует Docker и не занимает его ресурсы. Два варианта:
разовый запуск в терминале и запуск как systemd-сервис (может работать сколь
угодно долго, пока его не остановишь).

## Общая подготовка

```bash
cd <путь-к-репозиторию>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`TgCrypto` собирается из исходников — если сборка падает, поставьте
инструменты сборки и заголовки Python:

```bash
sudo apt install -y build-essential python3-dev
```

Заполните `.env` (см. `.env.example`), но `STORAGE_DIR` укажите на реальный
путь на диске (без Docker он не подставляется томом), например:

```
STORAGE_DIR=<путь-к-репозиторию>/storage
```

## Вариант 1 — разовый запуск

```bash
cd <путь-к-репозиторию>
source .venv/bin/activate
set -a; source .env; set +a
python bot.py
```

Останавливается `Ctrl+C`. Ничего не остаётся в фоне после закрытия терминала
(если не запущено внутри `tmux`/`screen`).

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
EnvironmentFile=<путь-к-репозиторию>/.env
ExecStart=<путь-к-репозиторию>/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

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
