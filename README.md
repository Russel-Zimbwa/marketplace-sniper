# Marketplace Sniper

**Get a Telegram message the moment a real bargain is posted on Facebook Marketplace near you – and skip the scams.**

You tell it what you want (`xbox series s`, `iphone 13 under 5000`, `road bike`). It checks Marketplace every minute or two, ignores anything that isn't freshly posted, works out what the item usually sells for, filters out accessories, wrong models and scams, and pings your phone with the good ones. One tap opens the listing so you can message the seller before anyone else.

Free. Runs on your own computer. No Facebook login needed.

---

## Why I built this

I found myself spending way too much time on Facebook Marketplace looking for a deal. And a lot of the time, when there *was* a good deal, it was either a scam or I was a little too late. That constant refreshing and hunting for the next deal was wasting a lot of my precious time.

So I used AI to help me design and code this app, with one goal: **give me my time back.** It does the watching for me and only taps me on the shoulder when something is genuinely worth it.

I hope it helps other people do the same – and I'd love for people to help make it better. Ideas, bug reports and pull requests are all welcome.

---

## What you get

A message like this, usually within a couple of minutes of the listing going up:

```
🔥🔥 STEAL · R3,400 · 6 min ago
Xbox Series X
About 50% under the usual R6,750 - you'd save about R3,350.

📍 Centurion, Gauteng · about 9 km away
📦 Used - like new

📷 Photo looks right: shows the console with two controllers

xbox series x · 9/10
[ Open listing · message seller ]
```

- **Only fresh listings.** Nothing older than 2 hours is ever sent (you can change this). Old listings are either sold or bait.
- **Knows the real price.** It learns what the item usually sells for near you, and tells you how much you'd save.
- **Right item only.** A Series S is not a Series X, a controller is not a console, a "wanted" ad is not for sale, a hire listing is not a sale.
- **Scam checks.** Listings priced too good to be true are checked for the usual signs, and labelled **⚠️ Too good to be true?** instead of being sold to you as a steal (more below).
- **Just type what you want.** Typos are fine. If a name covers several models (`garmin forerunner`, `iphone`), it shows you the models for sale with their prices and asks which one.
- **Price drops.** If a seller cuts the price on something posted recently, you hear about it.
- **Optional free AI photo check** that runs on your computer and warns you about catalogue photos or the wrong item.

---

## Install (about 10 minutes, no coding)

### Before you start: make your Telegram bot (2 minutes, on your phone)

1. Install [Telegram](https://telegram.org) if you don't have it.
2. Open a chat with **@BotFather**, send `/newbot`, pick any name, then a username ending in `bot`.
3. BotFather replies with a **token** (looks like `123456789:AAH...`). You'll paste it in during setup.

### Windows

1. Click the green **Code** button at the top of this page → **Download ZIP**.
2. Unzip it somewhere it can stay, e.g. `C:\MarketplaceSniper` (not inside Downloads, and not inside the zip).
3. Double-click **`SETUP.bat`**. It installs everything it needs (including Python, if you don't have it) and asks you a few questions:
   - your bot token,
   - to press **Start** on your bot in Telegram (this links it to your phone – nobody else can control it),
   - your city on Marketplace,
   - how far to look, and how fresh listings must be.
4. That's it. It runs in the background and starts by itself whenever you log in.

### Mac / Linux

```bash
git clone <this repository's address>   # or Download ZIP and unzip
cd marketplace-sniper
./setup.sh
```

### Then, in Telegram

Just type what you want:

```
xbox series s
iphone 13 under 5000
nintendo switch oled
```

It replies with the usual price, how many are for sale near you, and from then on messages you the moment a good one is posted.

---

## Telegram commands

| Send | What it does |
|---|---|
| `xbox series s` | Start watching an item (just type it) |
| `iphone 13 under 5000` | …with a maximum price |
| `list` | What you're watching |
| `stop 2` | Stop watching item 2 (or `stop xbox`) |
| `pause` / `resume` | Pause everything, or one item: `pause 2` |
| `fresh 60` | Only send listings posted in the last 60 minutes (also `fresh 3h`) |
| `radius 20` | Search distance in km |
| `far` | Switch "exceptional steals from further away" on/off; `far 100` sets how far |
| `scams` | Show or hide listings that look too good to be true |
| `threshold 7` | How good a deal must be to be sent (1–10) |
| `judge off` | Send every new nearby listing, not only good deals (`judge on` to go back) |
| `location london` | Change your Marketplace city |
| `status` | Is everything working? |
| `test 1` | Send a sample alert for item 1 |
| `help` | All of the above |

---

## How it decides

**1. Is it new?** Facebook includes the exact time each listing was posted. Anything older than your freshness window (2 hours by default) is used only to learn prices and is never sent. Each item is re-checked every 1–2 minutes.

**2. Is it the thing you asked for?** Built-in rules, no AI needed: the right model and variant (Series X ≠ Series S ≠ One X, 13 Pro ≠ 13 Pro Max), the item itself (not a controller, case, game, box or poster), actually for sale (not a "wanted" ad or a hire listing), and not broken, locked or swap-only. Different spellings are fine (`XboxseriesS`, `slushy` vs `slushie`).

**3. Is it a good price?** It keeps a running "usual price" from listings of the same item near you, ignoring joke and bait prices, and scores each listing 1–10:

| Score | Meaning |
|---|---|
| 9–10 | Steal – at least ~45% under the usual price |
| 8 | Great deal – 30–45% under |
| 7 | Good deal – 15–30% under |
| 5–6 | About normal |
| 1–4 | Overpriced, faulty, not the item |

You're sent anything that scores 7 or more (change with `threshold`).

**4. Is it too good to be true?** Anything 45% or more under the usual price is checked for signs of a scam:

| Strong signs | Doubts |
|---|---|
| Still unsold after 3+ days at that price | Brand-new seller account |
| Asks for payment, a deposit or a "courier/insurance fee" up front | Pushes you to WhatsApp, a phone number, email or Telegram |
| "I'm out of town, my brother will deliver" | Courier or deposit mentioned |
| Shipping only – you can't see it first | Claims to be brand new / sealed |
| Reads like a shop advert ("in stock", "each and every one comes with…") | The photo is a catalogue picture (with the photo check) |
| The same ad text is posted on several listings | More than 65% under the usual price |
| The seller has 3+ of the same item up at once | |

One strong sign or two doubts and it's labelled **⚠️ Too good to be true?**, sent silently (no buzz), with the reasons. A cheap listing with nothing else wrong – "emigrating, must go today" – is still a steal. Prices that aren't really the price ("installment available", "per month", "ask price", "R1") are never treated as bargains.

**5. Photo check (optional, free).** If you install [Ollama](https://ollama.com) (SETUP offers to), a small AI model on your computer looks at each alert's photo and adds a line to the message about 40–60 seconds later: "Photo looks right: …", or a warning that it's a catalogue picture or shows something else. It can add warnings but never hides or upgrades a listing on its own. It needs about 5 GB of disk and works best with 16 GB of memory.

---

## Run it all the time

- **Windows:** SETUP sets it to start when you log in and can keep the computer awake while plugged in. `STOP.bat` / `START.bat` stop and start it, `CONFIGURE.bat` changes your answers.
- **Mac / Linux:** `./start.sh` / `./stop.sh`. To start it when the computer starts, run `crontab -e` and add the line `@reboot /full/path/to/start.sh`.

For 24/7 use, a spare laptop that stays plugged in works well.

---

## Settings

Everything the setup wizard asks is saved in `.env` (see `.env.example` for every option, with explanations). Most settings can also be changed from Telegram. To change your answers: `CONFIGURE.bat` (Windows) or `./configure.sh`.

---

## Good to know

- **It browses Marketplace logged out**, like a visitor, so your Facebook account is never involved or at risk. If Facebook ever shows it a login wall, it backs off and retries by itself (5 min, 15, 30, 60, then every 2 hours) and only tells you if it keeps happening.
- **Automated browsing is against Facebook's terms.** This app only reads public pages, slowly, from your own internet connection. The worst likely outcome is Facebook temporarily showing a login wall. Keep it to a handful of items.
- **Facebook changes its pages often.** If `status` shows checks running but nothing is ever found, run `.venv\Scripts\python search_test.py "xbox series x"` (Mac/Linux: `.venv/bin/python search_test.py "xbox series x"`) to see what it sees, and check `logs/mpwatch.log`.
- **Prices are asking prices** on Marketplace, not what things actually sold for.
- **No guarantees.** The scam checks catch common patterns, not all of them. Always meet in a safe public place, check the item before paying, and never pay up front.

---

## Contributing

Ideas, bug reports and pull requests are very welcome – that's why this is public.

- **Found a listing it got wrong?** Open an issue with the title, price, description and what it should have done. The best fixes are the ones that come from real listings.
- **Code:** it's plain Python. The interesting parts:

| File | What it does |
|---|---|
| `mpwatch/main.py` | The watch loop: search, filter by distance and age, judge, alert |
| `mpwatch/judge_rules.py` | The rules: is it the item, condition, price score, scam signs |
| `mpwatch/judge.py` | Wraps the rules, plus the optional photo check through Ollama |
| `mpwatch/browser.py`, `parser.py` | Reading Marketplace with a headless browser |
| `mpwatch/bot.py` | Telegram commands and the alert layout |
| `mpwatch/geo.py`, `pricing.py`, `db.py` | Distances, the "usual price", storage |
| `setup_wizard.py`, `SETUP.bat`, `setup.sh` | Installing and first-time setup |

- **Tests:** `pip install pytest` then `python -m pytest tests -q`. Every rule has tests built from real listings; please add one with your change. Tests run automatically on every pull request.

Built with the help of AI ([Claude](https://claude.ai)). Released under the [MIT licence](LICENSE).
