"""Layer 1 of the free judge: deterministic rules. Pure functions, no I/O, works for any query.

judge_listing() answers three questions about one listing:
  1. Is it the item the buyer typed (right model, the thing itself, actually for sale)?
  2. Is there a concrete problem (broken, swap only, deposit, price bait...)?
  3. How does the price compare with the typical price?
"""
from __future__ import annotations

import datetime
import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# ---------- text normalisation ----------

_ALIASES = [
    (r"[‘’`]", "'"),
    (r"[–—]", "-"),
    (r"\bx\s+box\b", "xbox"),
    (r"\bxsx\b", "xbox series x"),
    (r"\bxss\b", "xbox series s"),
    (r"\bseries(x|s)\b", r"series \1"),
    (r"\bplay\s*station\b", "ps"),
    (r"\bi\s+phone\b", "iphone"),
    (r"\bmac\s+book\b", "macbook"),
    (r"\bair\s+pods\b", "airpods"),
    (r"\bpromax\b", "pro max"),
    (r"\bdisk\b", "disc"),
    (r"\bbicycles?\b", "bike"),
    (r"\btelevisions?\b", "tv"),
    (r"\bgroup\s+set\b", "groupset"),
    (r"\b(\d+)\s*(gb|tb)\b", r"\1\2"),
    (r"\b(\d+)\s*gigs?\b", r"\1gb"),
]
_TOKEN = re.compile(r"[a-z0-9]+|[+&]")
_STORAGE = re.compile(r"\d+(gb|tb|mb)")


def normalise(text: str) -> str:
    t = (text or "").lower()
    for rx, rep in _ALIASES:
        t = re.sub(rx, rep, t)
    return t


def tokens(text: str) -> list[str]:
    """Lower-case word tokens. 'ps5' -> ps, 5; '13pro' -> 13, pro; '1tb' and '6s' stay whole."""
    out: list[str] = []
    for tok in _TOKEN.findall(normalise(text)):
        if tok in "+&" or _STORAGE.fullmatch(tok):
            out.append(tok)
            continue
        m = re.fullmatch(r"([a-z]+)(\d+)", tok) or re.fullmatch(r"(\d+)([a-z]{3,})", tok)
        if m:
            out.extend(m.groups())
        else:
            out.append(tok)
    return out


def _singular(t: str) -> str:
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        return t[:-1]
    return t


# ---------- vocabulary ----------

_QUERY_STOP = {"a", "an", "the", "for", "and", "with", "in", "of", "+", "&"}
_BRANDS = {"microsoft", "sony", "apple", "samsung", "nintendo", "huawei", "xiaomi", "google", "lg", "hp", "dell",
           "lenovo", "asus", "acer", "canon", "nikon"}
_FILLER = {"console", "edition", "new", "brand", "used", "the", "a", "my", "selling", "sale", "for", "original",
           "genuine", "sealed", "boxed", "good", "great", "condition", "excellent", "mint", "clean", "neat", "like",
           "bargain", "urgent", "cheap", "gaming", "second", "hand", "secondhand", "preowned", "pre", "owned",
           "immaculate", "perfect", "working", "unlocked", "slim", "wifi", "cellular", "gen", "generation",
           "ladies", "mens", "men", "women", "womens", "carbon", "aluminium", "alloy", "size", "medium", "large",
           "small", "still", "in", "box", "very", "almost", "as", "quick", "must", "go", "price", "reduced"}
_COLOURS = {"black", "white", "blue", "red", "green", "silver", "gold", "grey", "gray", "pink", "purple", "yellow",
            "midnight", "starlight", "graphite", "titanium", "orange"}
# A model word right after the item that the buyer did not type means a different product
_MODIFIERS = {"pro", "max", "plus", "ultra", "mini", "lite", "se", "fe", "xl", "air", "oled", "fold", "flip",
              "note", "junior", "kids", "portal"}
_OPPOSITE = {"digital": {"disc"}, "disc": {"digital"}}
_CONNECT = {"with", "w", "and", "+", "&", "plus", "incl", "including", "includes", "included", "inc", "comes",
            "bundle", "combo", "in", "n"}
_INCLUDED = {"included", "incl", "includes", "inc"}
_ACCESSORY = {
    "controller", "controler", "remote", "case", "cover", "skin", "pouch", "sleeve", "poster", "sticker", "decal",
    "charger", "cable", "adapter", "adaptor", "stand", "dock", "mount", "holder", "headset", "strap", "band",
    "protector", "battery", "faceplate", "shell", "housing", "box", "game", "manual", "accessory",
    "helmet", "boot", "shoe", "jersey", "pedal", "saddle", "tyre", "tire", "tube", "pump", "rack", "carrier",
    "trainer", "groupset", "wheelset", "glove", "bag", "backpack", "keyboard", "lens",
    "dualsense", "dualshock", "joycon", "kinect", "vr",
}
_BOX_ONLY = re.compile(r"\b(box only|empty box|just the box|only the box|game only|disc only|case only|"
                       r"manual only|frame only|box for)\b")
_WANTED = re.compile(r"\b(wtb|wanted|looking for|in search of|iso|want to buy|searching for|i need|"
                     r"looking to buy)\b")
_PARTS_QUERY = {"broken", "faulty", "parts", "spares", "repair", "cracked", "damaged"}

# ---------- problem patterns ----------

_FUNCTIONAL = [
    r"\bbroken\b", r"\bfaulty\b", r"\bfaults?\b", r"\bnot working\b", r"\bstopped working\b",
    r"\bdoes(n'?t| not) (work|turn on|switch on|power on|read|charge)\b",
    r"\b(won'?t|will not|cant|can't|cannot) (work|turn on|switch on|power on|read|charge)\b",
    r"\bnot (turning|switching|powering) on\b",
    r"\bfor (parts|spares)\b", r"\bspares or repairs?\b",
    r"\bno (display|power|picture|sound|signal)\b",
    r"\b(dead (console|battery|phone)|completely dead)\b",
    r"\bneeds? (a |to be )?(repair|repairs|repaired|fixing|fixed|new screen|new battery|new hdmi)\b",
    r"\bwater damaged?\b", r"\bbanned\b", r"\bblacklisted\b", r"\bicloud lock\b",
    r"\b(icloud |network |sim |carrier |activation |passcode |password )?locked\b",  # not "unlocked"
]
_COSMETIC = [r"\bcracked\b", r"\bcracks?\b", r"\bdamaged?\b"]
_SWAP = [r"\bsw[ao]p(s|ping|pping|ped)?\b", r"\btrad(e|es|ing)\b", r"\bexchange for\b"]
_SWAP_SOFT = re.compile(r"\b(or (cash|sell|sale)|cash or|sell or|open to|also consider|might consider|"
                        r"would consider|will consider)\b")
_DEPOSIT = [r"\bdeposit\b"]
_COURIER_ONLY = [r"\b(courier|delivery|shipping|postage|pudo|paxi) only\b", r"\bno (collections?|viewings?)\b"]
_COURIER = [r"\b(pudo|paxi|postnet|courier guy|aramex)\b"]
_WHATSAPP = [r"\bwhats?app\b"]
_ISSUE = [r"\b(issue|problem)s?\b"]
_BAIT = re.compile(
    r"\bnot\b[^.\n]{0,25}\bfor (everything|all|the lot)\b|\bask (for )?(the )?prices?\b|"
    r"\bprice (is )?for the box\b|\b(inbox|dm|pm|message|contact|call|whatsapp)( me)? for (the |a )?prices?\b|"
    r"\bprice on request\b|\bprices? (vary|varies|differ)\b|"
    # the price shown is a deposit or a monthly payment, not the price
    r"\b(installments?|instalments?|lay-?bys?|per month|p/m|monthly payments?|rent to own|"
    r"starting (at|from)|prices? from|from only)\b")
_NEG = {"no", "not", "never", "without", "zero", "nothing", "non", "isnt", "aint", "wasnt", "neither", "nor"}
_PLACEHOLDERS = {"123", "1234", "12345", "123456", "1234567", "1111", "11111", "111111", "999999", "9999999"}


def _negated(text: str, start: int) -> bool:
    """True if the match is cancelled by a 'no / not / never' just before it in the same clause."""
    clause = re.split(r"[.!?\n;,]", text[:start])[-1]
    words = [w.replace("'", "") for w in re.findall(r"[a-z']+", clause)[-3:]]
    return any(w in _NEG for w in words)


def _hit(patterns: list[str], text: str) -> Optional[str]:
    for rx in patterns:
        for m in re.finditer(rx, text):
            if not _negated(text, m.start()):
                return m.group(0)
    return None


def placeholder_price(price: Optional[float], fair: Optional[float] = None) -> bool:
    """A number the seller typed just to fill the box (0, 1, 123, 1234...), not a real asking price."""
    if price is None:
        return False
    if price <= 1:
        return True
    s = str(int(price)) if price == int(price) else ""
    if s in _PLACEHOLDERS or (len(s) >= 7 and len(set(s)) == 1):
        return True
    return bool(fair and price < 0.02 * fair)


def correct_query(query: str, titles: list[str]) -> str:
    """Fix typos in what the buyer typed ('xbox seires s') using the words sellers actually use in
    the listings Facebook returned. No dictionary: works for any item."""
    vocab: Counter = Counter()
    for t in titles:
        vocab.update(set(tokens(t)))
    if not vocab:
        return " ".join(query.split())
    common = [w for w, c in vocab.items() if c >= max(2, len(titles) // 10) and w.isalpha() and len(w) >= 3]
    out = []
    for word in query.split():
        w = word.lower()
        if w.isalpha() and len(w) >= 3 and not all(t in vocab for t in tokens(w)):
            match = difflib.get_close_matches(w, common, n=1, cutoff=0.75)
            if match:
                out.append(match[0])
                continue
        out.append(word)
    return " ".join(out)


def swap_listing(title: str) -> bool:
    """Swap/trade ads carry made-up prices; they must not teach the typical price."""
    return _hit(_SWAP, normalise(title)) is not None


# ---------- is it the item? ----------

@dataclass
class Target:
    is_target: bool
    certain: bool = True   # False = a guess that a second opinion may overturn
    reason: str = ""       # why not (when is_target is False)
    note: str = ""         # caveat (when is_target is True)
    span: Optional[tuple[int, int]] = None
    title_tokens: list[str] = field(default_factory=list)


_HIRE = re.compile(r"\b(hire|hiring|rent|rental|rentals|to let)\b")


def _close(q: str, t: str) -> bool:
    """Same word, spelled a little differently: slushie / slushy / slushies, series / seried."""
    if not (q.isalpha() and t.isalpha()) or min(len(q), len(t)) < 5 or abs(len(q) - len(t)) > 3:
        return False
    common = next((i for i, (a, b) in enumerate(zip(q, t)) if a != b), min(len(q), len(t)))
    return common >= max(4, min(len(q), len(t)) - 1)


def _segment(t: str, qt: list[str]) -> Optional[list[str]]:
    """'xboxseriess' typed without spaces -> ['xbox', 'series', 's'], if it is made only of the query's words."""
    words = sorted(set(qt), key=len, reverse=True)

    def rec(rest: str, acc: list[str]) -> Optional[list[str]]:
        if not rest:
            return acc if len(acc) >= 2 else None
        for q in words:
            if rest.startswith(q):
                done = rec(rest[len(q):], acc + [q])
                if done:
                    return done
        return None
    return rec(t, []) if 4 <= len(t) <= 40 else None


def _align(tt: list[str], qt: list[str]) -> list[str]:
    """Rewrite title words into the buyer's words where they are plainly the same thing."""
    out: list[str] = []
    for t in tt:
        if t in qt:
            out.append(t)
            continue
        seg = _segment(t, qt)
        if seg:
            out.extend(seg)
            continue
        out.append(next((q for q in qt if _close(q, t)), t))
    return out


def _find_span(tt: list[str], qt: list[str]) -> Optional[tuple[int, int]]:
    n = len(qt)
    for i in range(len(tt) - n + 1):
        if tt[i:i + n] == qt:
            return (i, i + n - 1)
    if all(q in tt for q in qt):
        idx = [tt.index(q) for q in qt]
        return (min(idx), max(idx))
    return None


def _soft_missing(tok: str, tt: list[str]) -> bool:
    """A query word the title may simply have left out (brand, colour, storage, edition)."""
    if tok in _BRANDS or tok in _FILLER:
        return True
    if _STORAGE.fullmatch(tok):
        return not any(_STORAGE.fullmatch(t) for t in tt)
    if tok in _COLOURS:
        return not any(t in _COLOURS for t in tt)
    if tok in _OPPOSITE:
        return not any(o in tt for o in _OPPOSITE[tok])
    return False


def _accessory(tt: list[str], span: Optional[tuple[int, int]], qt: list[str]) -> Optional[tuple[str, bool]]:
    """Returns (word, certain) if the title is selling an accessory rather than the item."""
    q_single = {_singular(q) for q in qt}
    for i, t in enumerate(tt):
        base = _singular(t)
        if base not in _ACCESSORY or base in q_single:
            continue
        if base == "game" and i + 1 < len(tt) and tt[i + 1] == "console":
            continue
        if base == "box" and i > 0 and tt[i - 1] in _CONNECT:
            continue
        nxt = tt[i + 1] if i + 1 < len(tt) else ""
        if nxt == "for" and any(x in qt for x in tt[i + 2:i + 8]):
            return t, True  # "controller for series x"
        if nxt in _INCLUDED:
            continue
        if span is None:
            return t, False
        a, b = span
        if i > b:
            between = tt[b + 1:i]
            counted = i - 1 > b and tt[i - 1].isdigit() and int(tt[i - 1]) <= 9
            if any(x in _CONNECT for x in between) or counted:
                continue  # "... with 2 controllers" is a bundle ("245 strap" is not)
            return t, False
        if i < a and any(x in _CONNECT for x in tt[i + 1:a]):
            continue  # "2 controllers + xbox series x"
        return t, False
    return None


def classify_target(title: str, description: str, query: str) -> Target:
    qt = [t for t in tokens(query) if t not in _QUERY_STOP]
    tt = _align(tokens(title), qt)
    tgt = Target(True, title_tokens=tt)
    if not qt:
        return tgt
    ntitle = normalise(title)
    if not (set(qt) & {"wanted", "wtb"}):
        if _WANTED.search(ntitle) or _WANTED.match(normalise(description).lstrip()):
            return Target(False, True, "it's a 'wanted' ad, not something for sale", title_tokens=tt)
    if not (set(qt) & {"hire", "rent", "rental"}) and _HIRE.search(ntitle):
        return Target(False, True, "it's for hire, not for sale", title_tokens=tt)
    m = _BOX_ONLY.search(ntitle)
    if m and not (set(tokens(m.group(0))) & set(qt)):
        return Target(False, True, f"the title says '{m.group(0)}'", title_tokens=tt)

    span = _find_span(tt, qt)
    if span is None:
        missing = [q for q in qt if q not in tt]
        hard = [q for q in missing if not _soft_missing(q, tt)]
        if hard:
            # Sure it's a different product, or just a vague title? Sure: "Xbox Series S" (a model letter is
            # there, and it's the wrong one), "iPhone 13" when the buyer wants the Pro, "Xbox One X"
            # (another word sits where 'series' belongs). Only vague: "Xbox console", "Microsoft console
            # 1TB" - the description or a photo may still settle those.
            conflict = any(_modelish(t) and t not in qt and not _ignorable(t) for t in tt)
            certain = len(missing) < len(qt) and any(q in _MODIFIERS or (_modelish(q) and conflict) for q in hard)
            for q in hard:
                k = qt.index(q)
                if 0 < k < len(qt) - 1 and qt[k - 1] in tt and qt[k + 1] in tt \
                        and tt.index(qt[k + 1]) - tt.index(qt[k - 1]) >= 2:
                    certain = True
            if certain:
                return Target(False, True, f"it's a different model (the title has no '{' '.join(hard)}')",
                              title_tokens=tt)
            dt = tokens(description)
            n = len(qt)
            in_desc = any(dt[i:i + n] == qt for i in range(len(dt) - n + 1))
            if in_desc and not _accessory(tt, None, qt):
                tgt.note = "the title is vague but the description names it"
                return tgt
            # Fewer than half the buyer's words in the title: unrelated, and not worth a second look.
            # (A photo model once called a PS5 controller an Xbox Series X on a title like that.)
            vague = 2 * (len(qt) - len(missing)) >= len(qt)
            return Target(False, not vague,
                          f"the title doesn't mention '{' '.join(hard)}', so it may be something else",
                          title_tokens=tt)
        present = [tt.index(q) for q in qt if q in tt]
        span = (min(present), max(present)) if present else None
        tgt.note = f"the title doesn't say '{' '.join(missing)}' - check it's the right version"
    tgt.span = span

    if span is not None:
        b = span[1]
        nxt = tt[b + 1] if b + 1 < len(tt) else ""
        after = tt[b + 2] if b + 2 < len(tt) else ""
        is_mod = nxt in _MODIFIERS and nxt not in qt
        if is_mod and nxt == "plus" and after and not (after in _FILLER or after in _COLOURS
                                                        or _STORAGE.fullmatch(after) or after.isdigit()):
            is_mod = False  # "iphone 13 plus charger": 'plus' is just 'and'
        if is_mod:
            return Target(False, True, f"it's the '{nxt}' model, a different product", title_tokens=tt)
    acc = _accessory(tt, span, qt)
    if acc:
        # Final: the small photo model cannot be trusted to overrule this (it passed a WWE game as a console)
        return Target(False, True, f"it looks like an accessory ({acc[0]}), not the item itself", title_tokens=tt)
    return tgt


# ---------- broad names: which models are out there? ----------

@dataclass
class Variant:
    label: str             # "245", "series x", "13 pro max"
    query: str             # the full name to watch: "garmin forerunner 245"
    count: int
    price: Optional[float]  # median asking price


_STORAGE_NUMBERS = {"16", "32", "64", "128", "256", "512", "1024"}


def _modelish(t: str) -> bool:
    return (t.isdigit() or (len(t) == 1 and t.isalpha()) or bool(re.fullmatch(r"\d+[a-z]{1,2}", t))
            or t in _MODIFIERS)


def _ignorable(t: str) -> bool:
    return (t in _FILLER or t in _COLOURS or t in _CONNECT or t in _QUERY_STOP or t in _STORAGE_NUMBERS
            or bool(_STORAGE.fullmatch(t)) or _singular(t) in _ACCESSORY)


def find_variants(query: str, items: list[tuple[str, Optional[float]]], max_variants: int = 8) -> list[Variant]:
    """If the name the buyer typed covers several different models ('garmin forerunner' -> 35, 245, 745),
    return them with how many are for sale and their usual price. Empty list = the name is specific enough.
    Entirely data-driven: the models come from the listings, not from a product catalogue."""
    qt = [t for t in tokens(query) if t not in _QUERY_STOP]
    n = len(qt)
    if not n:
        return []
    labels: list[tuple[tuple[str, ...], Optional[float]]] = []
    for title, price in items:
        tt = tokens(title)
        b = next((i + n - 1 for i in range(len(tt) - n + 1) if tt[i:i + n] == qt), None)
        if b is None or _WANTED.search(normalise(title)) or _accessory(tt, (b - n + 1, b), qt):
            continue
        rest, label, j = tt[b + 1:], [], 0
        if rest and not _ignorable(rest[0]):
            if _modelish(rest[0]):
                label, j = [rest[0]], 1
            elif len(rest) > 1 and rest[0].isalpha() and len(rest[0]) >= 3 and _modelish(rest[1]) \
                    and not _ignorable(rest[1]):
                label, j = rest[:2], 2   # "series x", "forerunner 245", "galaxy s"
            while label and j < len(rest) and len(label) < 4 and _modelish(rest[j]) and not _ignorable(rest[j]):
                label.append(rest[j])
                j += 1
        labels.append((tuple(label), price if price and not placeholder_price(price) else None))

    # A long label seen only once is over-specific; fold it into its shorter form
    # ("pro 2019" + "pro 13" -> "pro"). A one-word label seen once is kept: it is a real model ("35").
    while True:
        counts = Counter(lab for lab, _ in labels)
        rare = [i for i, (lab, _) in enumerate(labels) if len(lab) > 1 and counts[lab] < 2]
        if not rare:
            break
        for i in rare:
            labels[i] = (labels[i][0][:-1], labels[i][1])
    found = [(lab, c) for lab, c in counts.items() if lab]
    total = len(labels)
    # Broad = most listings carry a model after the name, and there is more than one model
    if total < 6 or len(found) < 2 or sum(c for _, c in found) / total < 0.6:
        return []
    out = []
    for lab, c in found:
        prices = sorted(p for l, p in labels if l == lab and p)
        med = prices[len(prices) // 2] if prices else None
        out.append(Variant(" ".join(lab), f"{' '.join(query.split())} {' '.join(lab)}", c, med))
    out.sort(key=lambda v: (-v.count, v.price or 0))
    return out[:max_variants]


# ---------- the verdict ----------

@dataclass
class RuleResult:
    is_target: bool
    target_certain: bool
    condition: str
    red_flags: list[str]
    fair_price: Optional[float]
    score: int
    summary: str
    price_known: bool
    likely_scam: bool = False


_AGE = re.compile(r"\blisted\s+(a|an|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago")
_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def age_days(text: str) -> Optional[float]:
    """How long the listing has been up, from Facebook's 'Listed 2 weeks ago' line."""
    m = _AGE.search(normalise(text))
    if not m:
        return None
    return (1 if m.group(1) in ("a", "an") else int(m.group(1))) * _DAYS[m.group(2)]


_PAY_FIRST = re.compile(
    r"\b(pay(ment)? (first|upfront|up front|before (delivery|collection|viewing))|pay before|"
    r"eft (first|before|only)|proof of payment|send (the )?money|cash ?send|e-?wallet|instant money|"
    r"paypal|gift cards?|crypto|bitcoin|usdt|(delivery|courier|insurance|transport|admin|clearance) fees?)\b")
_AWAY = re.compile(
    r"\b(out of town|overseas|abroad|not (in|around) (the )?(country|town|area)|deployed|on deployment|"
    r"working away|(i'?m|i am) (away|not around)|currently (in|away|abroad)|"
    r"my (brother|sister|cousin|son|daughter|husband|wife|agent|driver|friend) will (deliver|bring|send|drop))\b")
_SHOP = re.compile(
    r"\b(in stock|stock available|each and every|while stocks? last|limited stock|order now|wholesale|bulk|"
    r"nationwide delivery|deliver(y|ies)? nationwide|on special|big special|promotion|promo|"
    r"factory sealed stock|grade a stock|we (sell|have|deliver))\b")
_CONTACT = re.compile(
    r"(\+27|\b0)\s?[6-8]\d[\s-]?\d{3}[\s-]?\d{4}\b|\b\d{10}\b|"
    r"[\w.+-]+@[\w-]+\.[\w.]+|\bt\.me/|\btelegram\b|\bsignal app\b")


@dataclass
class Context:
    """What is known about a listing beyond its words, from Facebook's data and earlier searches."""
    age_minutes: Optional[float] = None   # since it was posted
    shipping_only: bool = False
    seller_listings: int = 1              # of this item, by this seller, in this search
    duplicate_ads: int = 0                # other listings with the same description, word for word
    stock_photo: bool = False             # the photo check saw a catalogue/advert picture


def scam_signals(text: str, pct: float, flags: list[str], ctx: Optional[Context] = None) -> list[tuple[int, str]]:
    """Reasons to doubt a listing priced well under the usual, each with a weight (2 = strong, 1 = a doubt).
    A real steal is gone within hours, from an ordinary seller you can meet. Bait sits for days, comes from
    new accounts or 'shops', wants money first or a move off Facebook, and is often posted many times."""
    ctx = ctx or Context()
    why: list[tuple[int, str]] = []
    days = ctx.age_minutes / 1440 if ctx.age_minutes is not None else age_days(text)
    if days is not None and days >= 3:
        why.append((2, f"it has been up for {days:.0f} days and is still unsold at this price"))
    if _hit([_PAY_FIRST.pattern], text):
        why.append((2, "it asks for payment or a fee before you get it"))
    if _hit([_AWAY.pattern], text):
        why.append((2, "the seller says they're away and someone else will deliver"))
    if ctx.shipping_only:
        why.append((2, "it can only be shipped - you can't see it first"))
    if ctx.duplicate_ads:
        why.append((2, f"the same ad text is on {ctx.duplicate_ads + 1} different listings"))
    if ctx.seller_listings >= 3:
        why.append((2, f"this seller has {ctx.seller_listings} of these for sale at once"))
    if any("new account" in f for f in flags):
        why.append((1, "the seller's Facebook account is new"))
    if any("WhatsApp" in f for f in flags) or _CONTACT.search(text):
        why.append((1, "it pushes you to contact the seller off Facebook"))
    if any("courier" in f.lower() or "deposit" in f for f in flags):
        why.append((1, "it involves a courier or deposit"))
    if _SHOP.search(text):
        why.append((2, "it reads like a shop advert, not a person selling their own"))
    if re.search(r"\b(brand new|sealed|unopened)\b", text):
        why.append((1, "it claims to be brand new"))
    if ctx.stock_photo:
        why.append((1, "the photo is a catalogue picture, not the seller's own"))
    if pct >= 0.65:
        why.append((1, "it is more than 65% under the usual price"))
    return why


def is_scam(why: list[tuple[int, str]]) -> bool:
    return sum(w for w, _ in why) >= 2


def _condition(text: str, functional: bool, cosmetic: bool) -> str:
    if functional:
        return "faulty / for parts"
    # Facebook's own "Condition" field has four fixed values; anything else after the word is seller prose
    m = re.search(r"\bcondition\s*\n\s*(new|used\s*-\s*(?:like new|good|fair))\s*(?:\n|$)", text)
    if m:
        cond = m.group(1).strip()
    elif re.search(r"\b(brand new|sealed|unopened)\b", text):
        cond = "new"
    elif re.search(r"\b(like new|as new|mint|excellent|immaculate)\b", text):
        cond = "like new"
    elif re.search(r"\b(good|great|perfect) (working )?(condition|order)\b", text):
        cond = "good"
    else:
        cond = "unknown"
    if cosmetic:
        cond = "damaged" if cond == "unknown" else f"{cond}, damage mentioned"
    elif _hit([r"\b(working|works)\b"], text):
        cond = "working" if cond == "unknown" else f"{cond}, working"
    return cond


def _money(value: float, price_text: str) -> str:
    m = re.match(r"\s*([^\d\s]+)", price_text or "")
    return f"{m.group(1) if m else ''}{value:,.0f}"


def _price_score(pct: float) -> int:
    for floor, score in ((0.60, 10), (0.45, 9), (0.30, 8), (0.15, 7), (0.05, 6), (-0.10, 5), (-0.25, 4)):
        if pct >= floor:
            return score
    return 3


def judge_listing(title: str, description: str, price: Optional[float], price_text: str, query: str,
                  max_price: Optional[float] = None, typical: Optional[float] = None,
                  assume_target: bool = False, today: Optional[datetime.date] = None,
                  ctx: Optional[Context] = None) -> RuleResult:
    title, description, price_text = title or "", description or "", price_text or "?"
    text = normalise(f"{title}\n{description}")
    qt = set(tokens(query))
    flags: list[str] = []
    caps: list[tuple[int, str]] = []  # (highest score allowed, why)

    # --- concrete problems (negation-aware) ---
    functional = cosmetic = None
    if not (qt & _PARTS_QUERY):
        functional = _hit(_FUNCTIONAL, text)
        cosmetic = None if functional else _hit(_COSMETIC, text)
    if functional:
        flags.append(f"faulty or for parts ('{functional}')")
        caps.append((2, f"the listing says '{functional}'"))
    elif cosmetic:
        flags.append(f"damage mentioned ('{cosmetic}')")
        caps.append((3, f"the listing mentions damage ('{cosmetic}')"))
    elif _hit(_ISSUE, text):
        flags.append("mentions a problem - read the description")
    if not (qt & {"swap", "trade"}) and _hit(_SWAP, text):
        if _SWAP_SOFT.search(text):
            flags.append("open to swaps as well as cash")
        else:
            flags.append("wants a swap/trade, not a sale")
            caps.append((3, "the seller wants a swap, not a sale"))
    if _hit(_DEPOSIT, text):
        flags.append("asks for a deposit")
        caps.append((4, "the seller asks for a deposit"))
    if _hit(_COURIER_ONLY, text):
        flags.append("courier only - you can't view it first")
        caps.append((4, "it's courier only, so you can't see it first"))
    elif _hit(_COURIER, text):
        flags.append("courier (Pudo/Paxi) mentioned")
    if _hit(_WHATSAPP, text):
        flags.append("asks to continue on WhatsApp")
    m = re.search(r"joined facebook in (\d{4})", text)
    if m and int(m.group(1)) >= (today or datetime.date.today()).year - 1:
        flags.append(f"new account - seller joined Facebook in {m.group(1)}")

    # --- is the price real? ---
    price_known = price is not None
    if _BAIT.search(text):
        flags.append("price bait - the headline price is not the real price")
        price_known = False
    if placeholder_price(price, typical):
        flags.append("placeholder price - ask the seller")
        price_known = False

    # --- is it the item? ---
    tgt = classify_target(title, description, query)
    if assume_target and not tgt.is_target and not tgt.certain:
        tgt = Target(True, note="the title is vague; the photo check says it's the item, so look closely",
                     span=tgt.span, title_tokens=tgt.title_tokens)
    if tgt.is_target and tgt.span and typical and price_known and price < 0.10 * typical:
        # "PS5 Fifa 2026" at R600: the name is there, but so is something else, at a tenth of the price
        extra = tgt.title_tokens[:tgt.span[0]] + tgt.title_tokens[tgt.span[1] + 1:]
        if any(not (t in _BRANDS or t in _FILLER or t in _COLOURS or t in _CONNECT or t in _QUERY_STOP
                    or t.isdigit() or _STORAGE.fullmatch(t)) for t in extra):
            tgt = Target(False, True, f"at {price_text} it's under a tenth of the usual price and the title "
                         f"names something else too - probably a game or accessory for it")

    condition = _condition(text, bool(functional), bool(cosmetic))
    name = " ".join(query.split())
    if not tgt.is_target:
        return RuleResult(False, tgt.certain, condition, flags, typical, 1,
                          f"Not the {name}: {tgt.reason}.", price_known)

    # --- score ---
    pct = None
    # The summary is the one sentence under the price in the alert, so it doesn't repeat the price or the name
    scam = False
    if not price_known:
        score = 5
        summary = "The price shown isn't the real one - ask the seller what they actually want"
    elif typical:
        pct = round(1 - price / typical, 4)
        score = _price_score(pct)
        usual = _money(typical, price_text)
        if pct >= 0.05:
            summary = (f"About {round(pct * 100)}% under the usual {usual} - "
                       f"you'd save about {_money(typical - price, price_text)}")
        elif pct > -0.10:
            summary = f"About the usual price ({usual})"
        else:
            summary = f"About {round(-pct * 100)}% over the usual {usual}"
    else:
        score = 7
        summary = "No price history for this search yet, so judge the price yourself"
    if caps and min(caps)[0] < score:
        score, why = min(caps)
        summary += f" - but {why}"
    elif pct is not None and pct >= 0.45:
        why = scam_signals(text, pct, flags, ctx)
        if is_scam(why):
            scam = True
            reasons = [r for _, r in sorted(why, key=lambda x: -x[0])]
            summary = (f"About {round(pct * 100)}% under the usual {usual}, which looks too good to be true: "
                       + "; ".join(dict.fromkeys(reasons[:3])))
        if pct >= 0.5:
            flags.append("far below the usual price - verify in person before paying")
    if ctx and ctx.shipping_only and not scam:
        flags.append("shipping only - you can't see it before paying")
    if tgt.note:
        summary += f" ({tgt.note})"
    return RuleResult(True, True, condition, flags, typical, max(1, min(10, score)), summary + ".", price_known,
                      scam)
