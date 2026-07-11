# Pi Telegram Monitor Bot

A Telegram bot that monitors a Raspberry Pi (temperature, RAM, disk, services,
Docker containers) and can restart things, run speed tests, and send
scheduled reports.

## Setup

### 1. Create your bot
- Message **@BotFather** on Telegram → `/newbot` → follow the prompts → copy the token
- Message **@userinfobot** → it replies with your numeric Telegram user ID

### 2. Configure secrets
```bash
cp .env.example .env
```
Edit `.env` and fill in `BOT_TOKEN` and `ALLOWED_USER_IDS`. Never commit this file.

### 3. Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run it
```bash
python pi_bot.py
```

Send `/start` to your bot in Telegram to confirm it's alive.

### 5. Edit behavior config (optional)
Open `pi_bot.py` and adjust the CONFIG block: `WATCHED_SERVICES`,
`ALLOWED_COMMANDS`, `TEMP_ALERT`, `DISK_ALERT`, `DOCKER_WHITELIST`,
`SCHEDULED_TASKS`, `TIMEZONE`.

## Deploying on the Pi permanently

Allow passwordless sudo for just the commands the bot needs:
```bash
sudo visudo -f /etc/sudoers.d/pibot
```
```
pi ALL=(ALL) NOPASSWD: /usr/sbin/reboot, /usr/sbin/shutdown, /usr/bin/systemctl restart ssh, /usr/bin/systemctl restart nginx
```

Add your user to the `docker` group so Docker commands work without sudo:
```bash
sudo usermod -aG docker $USER   # log out/in for this to take effect
```

Create a systemd service so it starts on boot and restarts on crash:
```ini
# /etc/systemd/system/pibot.service
[Unit]
Description=Telegram Pi Monitor Bot
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/pi-bot
ExecStart=/home/pi/pi-bot/venv/bin/python /home/pi/pi-bot/pi_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now pibot
sudo systemctl status pibot     # check it's running
journalctl -u pibot -f          # tail logs
```

## Commands

| Command | Description |
|---|---|
| `/status` | CPU temp, load, RAM, disk, uptime |
| `/services` | Status of watched systemd services |
| `/restart <service>` | Restart a whitelisted service |
| `/run <name>` | Run a whitelisted shell command |
| `/ip` | Local + public IP |
| `/docker` | List containers and status |
| `/dstart /dstop /drestart <name>` | Control a container |
| `/dlogs <name>` | Last 30 lines of container logs |
| `/speedtest` | Run an internet speed test |
| `/reboot` / `/shutdown` | Restart or power off (confirmation required) |

## Project layout

```
pi-bot/
├── pi_bot.py           # main bot
├── requirements.txt
├── .env.example        # template - copy to .env and fill in
├── .env                # your secrets (gitignored, not committed)
├── .gitignore
├── DEPRECATED.md        # notice for the old telebot-based script
```
