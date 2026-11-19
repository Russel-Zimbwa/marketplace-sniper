"""Marketplace Sniper - first-time setup.

Asks a few questions and writes them to .env. Safe to run again any time to change your answers
(Windows: double-click CONFIGURE.bat; Mac/Linux: ./configure.sh).
"""
import re
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # run before the packages were installed
    sys.exit("Run SETUP.bat (Windows) or ./setup.sh (Mac/Linux) first - it installs what this needs.")

ROOT = Path(__file__).resolve().parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
API = "https://api.telegram.org/bot{token}/{method}"


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def write_env(values: dict) -> None:
    """Fill in the answers, keeping the comments and every other setting from .env.example."""
    lines, done = [], set()
    template = (EXAMPLE if EXAMPLE.exists() else ENV).read_text(encoding="utf-8").splitlines()
    for line in template:
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in values:
            lines.append(f"{m.group(1)}={values[m.group(1)]}")
            done.add(m.group(1))
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ask(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{hint}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def yes(question: str, default: bool = True) -> bool:
    answer = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not answer else answer.startswith("y")


def telegram(token: str, method: str, **params):
    r = httpx.get(API.format(token=token, method=method), params=params, timeout=40)
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


def get_token(current: str) -> tuple[str, str]:
    print("\n1) YOUR TELEGRAM BOT")
    print("   The app messages you through your own Telegram bot. To make one (takes a minute):")
    print("   - in Telegram, open a chat with @BotFather and send:  /newbot")
    print("   - pick any name, then a username ending in 'bot'")
    print("   - BotFather replies with a token like 123456789:AAH...  - copy it\n")
    while True:
        token = ask("   Paste the token here", current if current else "")
        if not re.fullmatch(r"\d{5,}:[\w-]{30,}", token):
            print("   That doesn't look like a bot token (numbers, a colon, then a long code). Try again.")
            continue
        try:
            me = telegram(token, "getMe")
        except httpx.HTTPError as e:
            print(f"   Couldn't reach Telegram ({e}). Check your internet and try again.")
            continue
        if me.get("ok"):
            name = me["result"].get("username", "")
            print(f"   OK - that's your bot @{name}")
            return token, name
        print("   Telegram doesn't recognise that token. Copy it again from BotFather.")


def get_chat_id(token: str, bot_name: str, current: str) -> str:
    print("\n2) CONNECT YOUR PHONE")
    if current and yes(f"   Already linked to a Telegram chat. Keep it?"):
        return current
    print(f"   Open Telegram, find @{bot_name}, press START (or send it any message).")
    print("   Waiting for your message (up to 3 minutes)...")
    try:
        offset = None
        old = telegram(token, "getUpdates", timeout=0).get("result", [])
        if old:
            offset = old[-1]["update_id"] + 1
        end = time.time() + 180
        while time.time() < end:
            params = {"timeout": 25}
            if offset:
                params["offset"] = offset
            for upd in telegram(token, "getUpdates", **params).get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = msg.get("chat", {})
                if chat.get("id"):
                    telegram(token, "getUpdates", offset=offset, timeout=0)  # don't leave it for the app
                    who = chat.get("first_name") or chat.get("title") or "you"
                    telegram(token, "sendMessage", chat_id=chat["id"],
                             text=f"✅ Linked! Only this chat can control the bot. Finish the setup on the computer.")
                    print(f"   Got it - linked to {who}'s chat. Only that chat can control the bot.")
                    return str(chat["id"])
    except httpx.HTTPError as e:
        print(f"   Couldn't check ({e}).")
    print("   No message arrived. That's fine: the first chat that messages the bot becomes its owner.")
    return ""


def get_location(current: str) -> str:
    print("\n3) WHERE YOU ARE")
    print("   Open facebook.com/marketplace in a browser and pick your city as the location.")
    print("   The address bar then shows  facebook.com/marketplace/<your-city>/  - type that word,")
    print("   e.g. pretoria, london, nyc, sydney, 108424279189115 (some places are a number).")
    while True:
        loc = ask("   Your city", current)
        m = re.search(r"marketplace/([A-Za-z0-9_\-]+)", loc)
        loc = (m.group(1) if m else loc).strip("/ ").lower().replace(" ", "")
        if re.fullmatch(r"[a-z0-9_\-]{2,40}", loc):
            return loc
        print("   Just the one word from the address bar, please.")


def get_number(question: str, current: str, lo: int, hi: int) -> str:
    while True:
        answer = ask(question, current)
        if answer.isdigit() and lo <= int(answer) <= hi:
            return answer
        print(f"   A number from {lo} to {hi}, please.")


def main() -> None:
    print("=" * 66)
    print(" MARKETPLACE SNIPER - setup")
    print(" Answer a few questions. Press Enter to keep the value in [brackets].")
    print("=" * 66)
    env = read_env(ENV)
    values = dict(env)  # keep every setting you already had; the answers below update it
    token, bot_name = get_token(env.get("TELEGRAM_BOT_TOKEN", ""))
    values["TELEGRAM_BOT_TOKEN"] = token
    keep_chat = env.get("TELEGRAM_CHAT_ID", "") if env.get("TELEGRAM_BOT_TOKEN") == token else ""
    values["TELEGRAM_CHAT_ID"] = get_chat_id(token, bot_name, keep_chat)
    values["LOCATION_ID"] = get_location(env.get("LOCATION_ID", ""))
    print("\n4) HOW FAR")
    values["RADIUS_KM"] = get_number("   Search radius in km (good deals within this distance)",
                                     env.get("RADIUS_KM", "10"), 1, 500)
    values["FAR_RADIUS_KM"] = get_number("   Also send exceptional steals from up to this many km (0 = never)",
                                         env.get("FAR_RADIUS_KM", "60"), 0, 1000)
    print("\n5) HOW FRESH")
    print("   Only listings posted within this many minutes are ever sent - old ones are usually gone.")
    values["FRESH_MINUTES"] = get_number("   Minutes", env.get("FRESH_MINUTES", "120"), 5, 2880)
    write_env(values)
    print("\nSaved to .env. You can run this again any time to change it.")
    print(f"Next: the app will message @{bot_name}. In Telegram just type what you want, e.g.  xbox series s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled - nothing changed.")
        sys.exit(1)
