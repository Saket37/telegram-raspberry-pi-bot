#!/usr/bin/env python3
"""
Raspberry Pi Telegram Monitor Bot
---------------------------------
Commands:
  /status   - CPU temp, load, RAM, disk, uptime
  /reboot   - Reboot the Pi (asks for confirmation)
  /shutdown - Shut down the Pi (asks for confirmation)
  /services - Check status of watched services
  /restart <service> - Restart a systemd service (whitelisted only)
  /run <cmd> - Run a whitelisted shell command
  /ip       - Show local + public IP
  /docker   - List containers and their status
  /dstart <name> /dstop <name> /drestart <name> - control a container
  /dlogs <name> - Last 30 lines of a container's logs
  /speedtest - Run an internet speed test (takes ~20-30s)
 
Also sends automatic alerts when:
  - CPU temp exceeds TEMP_ALERT °C
  - Disk usage exceeds DISK_ALERT %
  - A watched service goes down (and auto-restarts it)
 
Scheduled tasks (edit SCHEDULED_TASKS below):
  - Daily status report at a fixed time
  - Daily/weekly speed test with results pushed to you
"""

import os
import asyncio
import subprocess
import psutil
import time
import datetime
import urllib.request

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_HTTPS_KEY")
ALLOWED_USER_IDS = {int(uid.strip()) for uid in os.environ["ALLOWED_USERS"].split(",")}

# Services to watch and allow restarting
WATCHED_SERVICES = ["nginx", "ssh", "docker"]

# Whitelisted shell commands for /run (never allow arbitrary commands!)

ALLOWED_COMMANDS = {
    "uptime": "uptime",
    "who": "who",
    "df": "df -h",
    "docker ps": "docker ps",
}

DOCKER_WHITELIST = None


TEMP_ALERT = 70  # °C
DISK_ALERT = 90  # %
CHECK_INTERVAL = 60  # seconds between health checks

# Scheduled tasks - times are 24h "HH:MM" in the timezone set below.
TIMEZONE = "Asia/Kolkata"
SCHEDULED_TASKS = [
    {"type": "status", "time": "08:00"},      # daily status report
    {"type": "speedtest", "time": "08:05"},   # daily speed test result
]

def authorized(update: Update)-> bool:
    user = update.effective_user
    return user is not None and user.id in ALLOWED_USER_IDS

def get_cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except FileNotFoundError:
        return -1

def get_status_text() -> str:
    temp = get_cpu_temp()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    load1,load5,load15 = psutil.getloadavg()
    uptime_seconds = time.time() - psutil.boot_time()
    days, rem = divmod(int(uptime_seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return (
        f"🖥 *Pi Status*\n"
        f"🌡 CPU temp: {temp:.1f}°C\n"
        f"⚙️ Load: {load1:.2f} / {load5:.2f} / {load15:.2f}\n"
        f"🧠 RAM: {mem.percent}% used ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)\n"
        f"💾 Disk: {disk.percent}% used ({disk.free // (1024**3)}GB free)\n"
        f"⏱ Uptime: {days}d {hours}h {minutes}m"
    )


def service_status(name:str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", name])
    return r.returncode == 0

def docker_allowed(name:str) -> bool:
        return DOCKER_WHITELIST is None or name in DOCKER_WHITELIST


def docker_ps_text() -> str:
    r = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return f"Docker error:\n{r.stderr[:500]}"
    if not r.stdout.strip():
        return "No containers found."
    lines = []
    for line in r.stdout.strip().splitlines():
        name, status = line.split("\t", 1)
        icon = "🟢" if status.lower().startswith("up") else "🔴"
        lines.append(f"{icon} {name} - {status}")
    return "\n".join(lines)



def run_speedtest() -> str:
    """Blocking speed test - run in a background thread."""
    try:
        r = subprocess.run(
            ["speedtest-cli", "--simple"],
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode != 0:
            return f"speedtest-cli error:\n{r.stderr[:500]}"
        return r.stdout.strip()
    except FileNotFoundError:
        return "speedtest-cli not installed. Run: pip3 install speedtest-cli --break-system-packages"
    except subprocess.TimeoutExpired:
        return "Speed test timed out."


# ---------------- Command handlers ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Pi monitor online. Commands:\n"
        "/status /services /ip\n"
        "/restart <service>\n"
        "/run <name>\n"
        "/docker /dstart /dstop /drestart /dlogs <name>\n"
        "/speedtest\n"
        "/reboot /shutdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text = get_status_text()
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    lines = []
    for s in WATCHED_SERVICES:
        lines.append(f"{'🟢' if service_status(s) else '🔴'} {s}")
    await update.message.reply_text("\n".join(lines) or "No services configured.")
 

async def cmd_restart_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /restart <service>")
        return
    svc = context.args[0]
    if svc not in WATCHED_SERVICES:
        await update.message.reply_text(f"'{svc}' is not in the whitelist.")
        return
    r = subprocess.run(["sudo", "systemctl", "restart", svc],
                       capture_output=True, text=True)
    if r.returncode == 0:
        await update.message.reply_text(f"✅ Restarted {svc}")
    else:
        await update.message.reply_text(f"❌ Failed:\n{r.stderr[:500]}")

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not context.args:
        names = ", ".join(ALLOWED_COMMANDS)
        await update.message.reply_text(f"Usage: /run <name>\nAvailable: {names}")
        return
    name = context.args[0]
    cmd = ALLOWED_COMMANDS.get(name)
    if not cmd:
        await update.message.reply_text(f"'{name}' is not whitelisted.")
        return
    r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=30)
    output = (r.stdout or r.stderr or "(no output)")[:3500]
    await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")

async def cmd_docker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(docker_ps_text())


async def cmd_docker_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    if not authorized(update):
        return
    if not context.args:
        await update.message.reply_text(f"Usage: /d{action} <container>")
        return
    name = context.args[0]
    if not docker_allowed(name):
        await update.message.reply_text(f"'{name}' is not in the docker whitelist.")
        return
    r = subprocess.run(["docker", action, name], capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        await update.message.reply_text(f"✅ {action} {name}")
    else:
        await update.message.reply_text(f"❌ Failed:\n{r.stderr[:500]}")



async def cmd_dstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_docker_action(update, context, "start")

async def cmd_dstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_docker_action(update, context, "stop")


async def cmd_drestart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_docker_action(update, context, "restart")


async def cmd_dlogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /dlogs <container>")
        return
    name = context.args[0]
    if not docker_allowed(name):
        await update.message.reply_text(f"'{name}' is not in the docker whitelist.")
        return
    r = subprocess.run(
        ["docker", "logs", "--tail", "30", name],
        capture_output=True, text=True, timeout=15,
    )
    output = (r.stdout + r.stderr)[-3500:] or "(no output)"
    await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")


async def cmd_speedtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("📶 Running speed test, this takes ~20-30s...")
    result = await asyncio.get_running_loop().run_in_executor(None, run_speedtest)
    await msg.edit_text(f"📶 *Speed Test*\n```\n{result}\n```", parse_mode="Markdown")

async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): 
        return
    local = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.strip()
    try:
        public = urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode()
    except Exception:
        public = "unavailable"
    await update.message.reply_text(f"Local: {local}\nPublic: {public}")



# ---- Reboot/shutdown with confirmation buttons ----

async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm reboot", callback_data="do_reboot"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])
    await update.message.reply_text("Reboot the Pi?", reply_markup=kb)


async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm shutdown", callback_data="do_shutdown"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])
    await update.message.reply_text("Shut down the Pi?", reply_markup=kb)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ALLOWED_USER_IDS:
        await query.answer("Not authorized")
        return
    await query.answer()
    if query.data == "do_reboot":
        await query.edit_message_text("♻️ Rebooting now...")
        subprocess.Popen(["sudo", "reboot"])
    elif query.data == "do_shutdown":
        await query.edit_message_text("⏻ Shutting down...")
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
    else:
        await query.edit_message_text("Cancelled.")



# ---------------- Background health monitor ----------------

async def health_check(context: ContextTypes.DEFAULT_TYPE):
    alerts = []
 
    temp = get_cpu_temp()
    if temp > TEMP_ALERT:
        alerts.append(f"🌡 CPU temp high: {temp:.1f}°C")
 
    disk = psutil.disk_usage("/")
    if disk.percent > DISK_ALERT:
        alerts.append(f"💾 Disk at {disk.percent}%")
 
    for svc in WATCHED_SERVICES:
        if not service_status(svc):
            alerts.append(f"🔴 {svc} is DOWN - attempting restart...")
            subprocess.run(["sudo", "systemctl", "restart", svc])
            await asyncio.sleep(3)
            if service_status(svc):
                alerts.append(f"🟢 {svc} recovered")
            else:
                alerts.append(f"❌ {svc} failed to restart!")
 
    if alerts:
        msg = "⚠️ *Pi Alert*\n" + "\n".join(alerts)
        for uid in ALLOWED_USER_IDS:
            try:
                await context.bot.send_message(uid, msg, parse_mode="Markdown")
            except Exception:
                pass
 
 
async def notify_startup(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(uid, "🟢 Pi monitor bot started (Pi is up).")
        except Exception:
            pass




# ---------------- Scheduled tasks ----------------


async def scheduled_status(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(
                uid, "📅 *Daily Report*\n" + get_status_text(), parse_mode="Markdown"
            )
        except Exception:
            pass



async def scheduled_speedtest(context: ContextTypes.DEFAULT_TYPE):
    result = await asyncio.get_running_loop().run_in_executor(None, run_speedtest)
    for uid in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(
                uid, f"📶 *Scheduled Speed Test*\n```\n{result}\n```", parse_mode="Markdown"
            )
        except Exception:
            pass
 
 
SCHEDULED_TASK_FUNCS = {
    "status": scheduled_status,
    "speedtest": scheduled_speedtest,
}


def register_scheduled_tasks(app):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = None
    for task in SCHEDULED_TASKS:
        func = SCHEDULED_TASK_FUNCS.get(task["type"])
        if not func:
            print(f"Unknown scheduled task type: {task['type']}")
            continue
        hh, mm = map(int, task["time"].split(":"))
        run_time = datetime.time(hour=hh, minute=mm, tzinfo=tz)
        app.job_queue.run_daily(func, time=run_time)
        print(f"Scheduled '{task['type']}' daily at {task['time']} ({TIMEZONE})")



def main():
    # Python 3.14 removed the implicit event-loop creation that older asyncio
    # versions relied on, which trips up run_polling() internally calling
    # asyncio.get_event_loop(). Create and register one explicitly so it's
    # there when the library looks for it. Harmless no-op on older Python.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("restart", cmd_restart_service))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("ip", cmd_ip))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CommandHandler("shutdown", cmd_shutdown))
    app.add_handler(CommandHandler("docker", cmd_docker))
    app.add_handler(CommandHandler("dstart", cmd_dstart))
    app.add_handler(CommandHandler("dstop", cmd_dstop))
    app.add_handler(CommandHandler("drestart", cmd_drestart))
    app.add_handler(CommandHandler("dlogs", cmd_dlogs))
    app.add_handler(CommandHandler("speedtest", cmd_speedtest))
    app.add_handler(CallbackQueryHandler(on_button))
 
    app.job_queue.run_repeating(health_check, interval=CHECK_INTERVAL, first=30)
    app.job_queue.run_once(notify_startup, when=2)
    register_scheduled_tasks(app)
 
    app.run_polling()
 
 
if __name__ == "__main__":
    main()