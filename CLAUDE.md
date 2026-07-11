# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A single-file Telegram bot (`pi_bot.py`) that monitors and controls a Raspberry Pi over Telegram: system stats, systemd services, Docker containers, speed tests, and reboot/shutdown. Built on `python-telegram-bot` v21 (async).

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run (long-running foreground process; polls Telegram)
python pi_bot.py
```

There is no test suite, linter config, or build step. To exercise a change, run the bot and drive it from a Telegram client with an authorized user.

## Configuration

Two secrets come from a `.env` file (loaded via `python-dotenv`):
- `BOT_HTTPS_KEY` — the BotFather token (assigned to the `BOT_TOKEN` variable in code).
- `ALLOWED_USERS` — comma-separated numeric Telegram user IDs (parsed into the `ALLOWED_USER_IDS` set in code).

Behavior is tuned by module-level constants near the top of `pi_bot.py`: `WATCHED_SERVICES`, `ALLOWED_COMMANDS`, `DOCKER_WHITELIST` (`None` = allow all containers), `TEMP_ALERT`, `DISK_ALERT`, `CHECK_INTERVAL`, `TIMEZONE`, and `SCHEDULED_TASKS`.

## Architecture

Everything lives in `pi_bot.py`, organized into four conceptual sections:

1. **Pure helpers** (`get_status_text`, `service_status`, `docker_ps_text`, `run_speedtest`, etc.) — read the system via `psutil`, `/sys/class/thermal`, and `subprocess` calls to `systemctl` / `docker` / `speedtest-cli`. These do the actual work and return strings; handlers just relay them to Telegram.
2. **Command handlers** (`cmd_*`) — one async function per Telegram command, wired up in `main()`. Reboot/shutdown use an `InlineKeyboardMarkup` confirmation flow whose button presses are dispatched through the single `on_button` `CallbackQueryHandler`.
3. **Background jobs** — registered on the PTB `job_queue`: `health_check` runs every `CHECK_INTERVAL` seconds (alerts on temp/disk thresholds and auto-restarts down services), `notify_startup` runs once at boot.
4. **Scheduled tasks** — `SCHEDULED_TASKS` (a list of `{type, time}` dicts) is turned into daily `job_queue.run_daily` jobs by `register_scheduled_tasks`, dispatched via the `SCHEDULED_TASK_FUNCS` registry. Add a new scheduled kind by writing a `scheduled_*` coroutine and adding it to that dict.

### Key cross-cutting patterns

- **Authorization gate:** every handler starts with `if not authorized(update): return`. Any new command handler must do the same. `on_button` checks `query.from_user.id in ALLOWED_USER_IDS` directly.
- **Whitelisting for shell/Docker/services:** privileged actions never take arbitrary input. `/run` only executes commands in `ALLOWED_COMMANDS`, `/restart` only services in `WATCHED_SERVICES`, and Docker actions pass through `docker_allowed()`. Preserve this — do not add paths that shell out to user-supplied strings.
- **Blocking work off the event loop:** `run_speedtest` is synchronous/blocking, so it's dispatched via `run_in_executor` from the async handlers. Follow this pattern for any new long-running subprocess.
- **Broadcasts** to `ALLOWED_USER_IDS` (alerts, startup, scheduled reports) wrap each `send_message` in a bare `try/except` so one unreachable user doesn't break the loop.

### Deployment

Intended to run as a systemd service on the Pi with `Restart=always`, using passwordless sudo scoped to just `reboot`, `shutdown`, and specific `systemctl restart` commands (see README). Docker access relies on the running user being in the `docker` group.
