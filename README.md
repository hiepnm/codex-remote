# codex-remote

Telegram worker that mirrors `codex` CLI commands to your local machine.

## Overview

Send commands from Telegram and run them on your home machine through Codex CLI.

- Without secret: `/codex ...`
- With secret: `/codex <secret> ...`

This is useful when you are outside and still need to control your local Codex agent.

## Requirements

- Python 3.10+
- `codex` CLI installed and logged in on the same machine
- Telegram bot token (`TG_BOT_TOKEN`)
- Your Telegram chat ID (`TG_ALLOWED_CHAT_ID`)

## Get Telegram Values

1. Create a bot with [@BotFather](https://t.me/BotFather).
2. Send any message to your bot.
3. Open:
   - `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Read `chat.id` from JSON and use it as `TG_ALLOWED_CHAT_ID`.

## Configuration

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Set values:

```env
TG_BOT_TOKEN="..."
TG_ALLOWED_CHAT_ID="..."
COMMAND_SECRET=""
CODEX_BIN=""
WORKDIR="/absolute/path/to/default/workdir"
BOT_NAME="codex-remote"
OFFSET_FILE=".telegram_offset"
```

Variable notes:

- `COMMAND_SECRET`
  - Empty: command format is `/codex ...`
  - Set: command format is `/codex <secret> ...`
- `CODEX_BIN`
  - Empty: auto-detect Codex binary (`asdf which codex` first, then PATH)
  - Set: force exact Codex binary path
- `OFFSET_FILE`
  - File used to persist last Telegram update offset across restarts

## Run

Install dependencies:

```bash
pip install requests python-dotenv
```

Start worker:

```bash
python bot_worker.py
```

Dev auto-reload (nodemon-like):

```bash
python dev_runner.py
```

`dev_runner.py` watches `.py` files and `.env`, then restarts `bot_worker.py` automatically when files change.

For deployment, run it with a process manager (`systemd`, `supervisord`, `tmux`, `screen`, or Docker).

## Telegram Usage

- `/start` for quick help
- `/codex --help`
- `/codex exec "say hello"`
- `/codex -C /path/to/repo exec "..."`
- `/codex -view-mode html exec "..."`
- `/codex -view-mode pre exec "..."`
- With secret enabled: `/codex <secret> -view-mode <mode> ...`

View modes:

- `html` (default): header in copy box, answer rendered with Telegram HTML.
- `pre`: fixed-width `<pre>` block for full raw output.

Only `TG_ALLOWED_CHAT_ID` is accepted.

## Security Checklist

Apply these before using in production:

- Rotate `TG_BOT_TOKEN` if it was ever exposed.
- Enable Telegram 2FA, app passcode, and SIM protection.
- Set `COMMAND_SECRET` in `.env` for a second auth layer.
- Use command format `/codex <secret> ...` when `COMMAND_SECRET` is set.
- Rotate `COMMAND_SECRET` regularly, especially before each trip/outside session.
- Run worker with a dedicated OS user and minimum permissions.
- Do not run this worker on your primary user account that holds sensitive data.
- When you are at home, stop this worker and use local Codex directly.

## Incident Response

If you suspect exposure:

1. Stop worker immediately.
2. Rotate `TG_BOT_TOKEN` via BotFather.
3. Rotate `COMMAND_SECRET`.
4. Review recent Telegram chats and machine activity logs.
