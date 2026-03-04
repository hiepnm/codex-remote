# codex-remote

Small Telegram worker that mirrors `codex` CLI commands to your home/local machine.

You can send:

```text
/codex ...
```

and the worker runs the same command locally with Codex CLI, then sends output back to Telegram.

## What this tool is for

- Control your local Codex agent remotely from Telegram.
- Run Codex tasks when you are outside.
- Keep your main workflow on your own machine/repositories.

## Prerequisites

- Python 3.10+
- `codex` CLI installed and logged in on the same machine
- Telegram bot token
- Your Telegram chat ID

## Get Telegram info

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy `TG_BOT_TOKEN`.
2. Send a message to your bot from your Telegram account.
3. Open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`.
4. Find your `chat.id` in the JSON and use it as `TG_ALLOWED_CHAT_ID`.

## Setup

1. Clone repo and install deps:

```bash
pip install requests python-dotenv
```

2. Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

3. Edit `.env`:

```env
TG_BOT_TOKEN="..."
TG_ALLOWED_CHAT_ID="..."
COMMAND_SECRET=""
WORKDIR="/absolute/path/to/default/workdir"
BOT_NAME="codex-remote"
```

`COMMAND_SECRET` is optional:
- Empty -> use `/codex ...`
- Set value -> use `/codex <secret> ...`

## Run worker

```bash
python bot_worker.py
```

For deployment, keep it running with your preferred process manager (for example: `systemd`, `supervisord`, `tmux`, `screen`, or Docker).

## Telegram usage

- `/start` -> quick help
- `/codex --help` -> show Codex help
- `/codex exec "say hello"` -> run a simple Codex task
- `/codex -C /path/to/repo exec "..."` -> run in a specific repo

The worker only accepts messages from `TG_ALLOWED_CHAT_ID`.

## Security recommendation

- Set `COMMAND_SECRET` in `.env` for a second auth layer.
- When `COMMAND_SECRET` is set, every command must be: `/codex <secret> ...`.
- Rotate `COMMAND_SECRET` regularly (for example when you are about to go outside and run this worker).
- Stop this worker when you are at home and can run Codex directly on your machine.
