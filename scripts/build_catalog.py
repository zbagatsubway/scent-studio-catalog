#!/usr/bin/env python3
"""Build the Scent Studio supplier catalogue.

Reads a supplier's public Shopify product list, keeps the perfumery
materials, makes a first guess at note type / longevity / strength /
scent profiles, applies hand-written overrides, and writes:

    catalog/<supplier>.json   products
    catalog/rates.json        exchange rates from the supplier currency
    catalog/manifest.json     what the app checks first (versions + counts)

Standard library only, so it runs on a Mac (python3) and in GitHub Actions.

    python3 scripts/build_catalog.py                    # live fetch
    python3 scripts/build_catalog.py --from-file x.json # test with a saved products.json

Guesses are marked with "estimated": true in the output so the app can label
them; anything in overrides/<supplier>.json always wins.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "catalog"
OVERRIDES_DIR = ROOT / "overrides"

SCHEMA = 1
USER_AGENT = "ScentStudioCatalog/1.0 (+https://github.com/zbagatsubway/scent-studio-catalog)"
PAGE_DELAY_SECONDS = 2.0      # be polite: one request every 2 s
MAX_PAGES = 40                # 40 x 250 = 10,000 products, far above what's needed
MAX_DROP_FRACTION = 0.20      # refuse to publish if the product count falls >20%
DESCRIPTION_MAX_CHARS = 1500

# Include supplier-written descriptions? Ask the supplier before publishing
# these in a public repo. Turn off with INCLUDE_DESCRIPTIONS=0.
INCLUDE_DESCRIPTIONS = os.environ.get("INCLUDE_DESCRIPTIONS", "1") != "0"

SUPPLIERS = {
    "fraterworks": {
        "name": "Fraterworks",
        "country": "New Zealand",
        "website": "https://fraterworks.com",
        # Appended to product links by the app; fill in once a referral deal exists.
        "referralQuery": "",
    },
}

# ── Filtering ──────────────────────────────────────────────────────────────

EXCLUDE_WORDS = [
    "gift card", "gift-card", "kit", "journey", "sample set", "discovery set",
    "bottle", "atomiser", "atomizer", "pipette", "beaker", "scale", "funnel",
    "smelling strip", "blotter", "label", "merch", "t-shirt", "tote", "book",
    "course", "contract compounding", "shipping", "insurance", "donation",
    "perfumery colour", "perfume colour", "colorant", "colourant", "dye",
]

# ── Classification keywords (lowercase) ────────────────────────────────────

NOTE_KEYWORDS = {
    "top": [
        "citrus", "bergamot", "lemon", "lime", "grapefruit", "orange", "mandarin",
        "tangerine", "yuzu", "neroli", "petitgrain", "aldehyd", "aldehydic", "zesty",
        "sparkling", "eucalyptus", "mint", "menthol", "peppermint", "spearmint",
        "camphor", "ozonic", "marine", "aquatic", "cucumber", "juicy", "fizzy",
        "verdant", "galbanum", "cardamom", "pink pepper", "ginger",
    ],
    "middle": [
        "floral", "rose", "jasmin", "jasmine", "iris", "orris", "violet", "ionone",
        "lily", "muguet", "lilac", "geranium", "lavender", "ylang", "tuberose",
        "orange flower", "osmanthus", "magnolia", "peony", "carnation", "heliotrope",
        "mimosa", "spice", "spicy", "clove", "cinnamon", "nutmeg", "pepper",
        "herbal", "herbaceous", "green", "tea", "fruity", "peach", "apricot",
        "plum", "berry", "damascone", "hedione",
    ],
    "base": [
        "musk", "musky", "amber", "ambergris", "ambery", "ambrox", "wood", "woody",
        "cedar", "sandalwood", "sandal", "vetiver", "patchouli", "oud", "agarwood",
        "resin", "resinous", "resinoid", "balsam", "balsamic", "benzoin", "labdanum",
        "cistus", "vanilla", "vanillin", "coumarin", "tonka", "leather", "animalic",
        "civet", "castoreum", "oakmoss", "moss", "incense", "frankincense", "olibanum",
        "myrrh", "opoponax", "styrax", "tobacco", "honey", "fixative", "powdery",
        "smoky", "smoke", "birch tar", "guaiac", "cashmeran", "iso e super",
    ],
}

# Scent Studio's standard scent profiles → keywords.
PROFILE_KEYWORDS = {
    "Citrus":   ["citrus", "bergamot", "lemon", "lime", "grapefruit", "orange", "mandarin", "yuzu", "tangerine", "petitgrain"],
    "Floral":   ["floral", "rose", "jasmin", "jasmine", "iris", "orris", "violet", "lily", "muguet", "lilac", "geranium",
                 "ylang", "tuberose", "orange flower", "neroli", "osmanthus", "magnolia", "peony", "carnation", "mimosa", "hedione"],
    "Woody":    ["wood", "woody", "cedar", "sandalwood", "sandal", "vetiver", "patchouli", "oud", "agarwood", "guaiac", "iso e super", "cashmeran"],
    "Musky":    ["musk", "musky", "galaxolide", "helvetolide", "habanolide", "ethylene brassylate", "ambrettolide"],
    "Oriental": ["oriental", "amber", "ambery", "opoponax", "labdanum", "incense", "frankincense", "myrrh"],
    "Fresh":    ["fresh", "clean", "airy", "ozonic", "sparkling", "cool"],
    "Aquatic":  ["aquatic", "marine", "sea", "ocean", "calone", "watery", "ozone"],
    "Gourmand": ["gourmand", "vanilla", "vanillin", "caramel", "chocolate", "coffee", "honey", "coumarin", "tonka",
                 "praline", "licorice", "liquorice", "milk", "maltol", "ethyl maltol"],
    "Aromatic": ["aromatic", "lavender", "herbal", "herbaceous", "sage", "rosemary", "thyme", "basil", "mint", "eucalyptus", "camphor"],
    "Leather":  ["leather", "suede", "birch tar", "isobutyl quinoline"],
    "Spicy":    ["spice", "spicy", "clove", "cinnamon", "nutmeg", "pepper", "cardamom", "ginger", "saffron", "cumin"],
    "Green":    ["green", "galbanum", "leaf", "leafy", "grass", "grassy", "violet leaf", "tomato leaf", "cucumber", "stemmy"],
    "Powdery":  ["powdery", "powder", "iris", "orris", "ionone", "heliotrope", "talc", "violet"],
    "Animalic": ["animalic", "civet", "castoreum", "indolic", "indole", "skatole", "hyraceum", "fecal", "barnyard"],
    "Resinous": ["resin", "resinous", "resinoid", "balsam", "balsamic", "benzoin", "styrax", "peru", "tolu", "elemi"],
}

STRENGTH_WORDS = [
    ("Very strong", ["extremely powerful", "very powerful", "incredibly powerful", "potent", "high impact",
                     "use sparingly", "use with care", "tiny amounts", "trace amounts", "overpowering"]),
    ("Strong",      ["powerful", "strong", "diffusive", "intense", "radiant", "bold", "big"]),
    ("Soft",        ["soft", "subtle", "delicate", "mild", "gentle", "weak", "faint", "quiet", "low odour", "low odor"]),
]

LONG_WORDS = ["long-lasting", "long lasting", "tenacious", "excellent tenacity", "great tenacity",
              "substantive", "fixative", "lasts for days", "lasts for weeks", "enduring"]
SHORT_WORDS = ["fleeting", "volatile", "short-lived", "short lived", "evaporates quickly", "transparent top"]

SOLVENTS = {"tec": "TEC", "dpg": "DPG", "ipm": "IPM", "eth": "Ethanol", "ethanol": "Ethanol",
            "alcohol": "Ethanol", "dep": "DEP", "bb": "Benzyl Benzoate", "benzyl benzoate": "Benzyl Benzoate",
            "ipm": "IPM", "mct": "MCT", "jojoba": "Jojoba"}

CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gram|grams|ml|l)\b", re.I)


# ── Helpers ────────────────────────────────────────────────────────────────

def http_json(url: str, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def strip_html(raw: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", raw or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def count_hits(text: str, words: list[str]) -> int:
    return sum(1 for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text))


def weighted_score(parts: list[tuple[str, int]], words: list[str]) -> int:
    return sum(weight * count_hits(text, words) for text, weight in parts)


def parse_grams(variant: dict) -> float | None:
    """Size in grams from the variant title ("10 g", "1 kg", "5ml"); ml treated as g."""
    for field in ("title", "option1", "option2"):
        m = SIZE_RE.search(str(variant.get(field) or ""))
        if m:
            value, unit = float(m.group(1)), m.group(2).lower()
            if unit == "kg" or unit == "l":
                return value * 1000
            return value
    grams = variant.get("grams")
    return float(grams) if grams else None


def size_label(variant: dict, grams: float | None) -> str:
    title = str(variant.get("title") or "").strip()
    if title and title.lower() != "default title":
        return title
    if grams:
        return f"{grams:g} g"
    return ""


# ── Classification ─────────────────────────────────────────────────────────

def is_excluded(title: str, product_type: str, tags: list[str]) -> bool:
    hay = " ".join([title, product_type, " ".join(tags)]).lower()
    return count_hits(hay, EXCLUDE_WORDS) > 0


def classify_kind(title: str, product_type: str, tags: list[str]) -> str:
    t, pt, tg = title.lower(), product_type.lower(), " ".join(tags).lower()
    if "compound" in t or "compound" in pt or "accord" in pt:
        return "compound"
    if "base" in pt or re.search(r"(?<![a-z])bases?(?![a-z])", tg):
        return "base"
    natural_words = ["essential oil", "absolute", "resinoid", "co2", "attar", "tincture", "concrete",
                     "natural", "oil", "extract", "balsam", "resin"]
    if "natural" in pt or "natural" in tg or count_hits(t, natural_words):
        return "natural"
    if "aroma chemical" in pt or "aroma chemical" in tg or "synthetic" in tg or pt == "" or "chemical" in pt:
        return "aroma-chemical"
    return "other"


def guess_form(title: str, desc: str, dilution) -> str:
    text = (title + " " + desc).lower()
    if dilution:
        return "Dilution"
    for word, form in [("crystal", "Crystals"), ("powder", "Powder"), ("solid", "Solid"),
                       ("resinoid", "Resinoid"), ("resin", "Resin"), ("absolute", "Absolute"),
                       ("concrete", "Concrete"), ("co2", "CO2 extract"), ("essential oil", "Essential oil"),
                       (" oil", "Oil")]:
        if word in " " + text:
            return form
    return "Liquid"


def parse_dilution(title: str):
    m = PERCENT_RE.search(title)
    if not m:
        return None
    pct = float(m.group(1))
    if pct >= 100:
        return None
    rest = title[m.end():].lower()
    solvent = ""
    for key, name in SOLVENTS.items():
        if re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", rest):
            solvent = name
            break
    return {"percent": pct, "solvent": solvent}


def guess_note_type(parts) -> str | None:
    text = " ".join(t for t, _ in parts)
    explicit = [("top", ["top note", "head note"]), ("middle", ["heart note", "middle note"]),
                ("base", ["base note", "drydown", "dry-down"])]
    for note, words in explicit:
        if count_hits(text, words):
            return note
    scores = {note: weighted_score(parts, words) for note, words in NOTE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def guess_longevity(desc: str, note_type: str | None) -> str:
    text = desc.lower()
    m = re.search(r"(\d+)\s*(\+)?\s*(hours|hrs|hr|h)\b", text)
    if m and ("last" in text or "tenacity" in text or "on strip" in text or "blotter" in text):
        hours = int(m.group(1))
        return f"{hours}+ hours" if m.group(2) else f"About {hours} hours"
    m = re.search(r"(\d+)\s*(\+)?\s*(days|weeks)\b", text)
    if m and ("last" in text or "tenacity" in text or "blotter" in text or "strip" in text):
        return f"{m.group(1)}{m.group(2) or ''} {m.group(3)}"
    if count_hits(text, LONG_WORDS):
        return "Long"
    if count_hits(text, SHORT_WORDS):
        return "Short"
    return {"top": "Short", "middle": "Medium", "base": "Long"}.get(note_type or "", "")


def guess_strength(desc: str) -> str:
    text = desc.lower()
    for label, words in STRENGTH_WORDS:
        if count_hits(text, words):
            return label
    return "Medium"


def guess_profiles(parts) -> list[str]:
    scores = {p: weighted_score(parts, words) for p, words in PROFILE_KEYWORDS.items()}
    ranked = [p for p, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0]
    return ranked[:3]


# ── Normalise one product ──────────────────────────────────────────────────

def normalise(prod: dict, supplier_id: str, base_url: str, currency: str, overrides: dict):
    title = (prod.get("title") or "").strip()
    handle = prod.get("handle") or ""
    product_type = (prod.get("product_type") or "").strip()
    tags = prod.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    ov = overrides.get(handle, {})
    if ov.get("exclude") or (not ov.get("include") and is_excluded(title, product_type, tags)):
        return None

    desc = strip_html(prod.get("body_html") or "")
    parts = [(title.lower(), 3), (" ".join(tags).lower() + " " + product_type.lower(), 2), (desc.lower(), 1)]

    variants = []
    for v in prod.get("variants") or []:
        try:
            price = round(float(v.get("price")), 2)
        except (TypeError, ValueError):
            continue
        grams = parse_grams(v)
        variants.append({
            "id": v.get("id"),
            "size": size_label(v, grams),
            "grams": grams,
            "price": price,
            "available": bool(v.get("available", True)),
        })
    if not variants:
        return None
    variants.sort(key=lambda v: (v["grams"] is None, v["grams"] or 0, v["price"]))

    dilution = parse_dilution(title)
    note_guess = guess_note_type(parts)
    cas = CAS_RE.search(title + " " + desc)

    item = {
        "id": f"{supplier_id}:{prod.get('id')}",
        "handle": handle,
        "supplier": supplier_id,
        "name": title,
        "vendor": (prod.get("vendor") or "").strip(),
        "url": f"{base_url}/products/{handle}",
        "currency": currency,
        "kind": classify_kind(title, product_type, tags),
        "form": guess_form(title, desc, dilution),
        "cas": cas.group(1) if cas else "",
        "dilution": dilution,
        "noteType": note_guess or "middle",
        "longevity": guess_longevity(desc, note_guess),
        "strength": guess_strength(desc),
        "profiles": guess_profiles(parts),
        "estimated": ["noteType", "longevity", "strength", "profiles"],
        "description": desc[:DESCRIPTION_MAX_CHARS] if INCLUDE_DESCRIPTIONS else "",
        "tags": tags,
        "variants": variants,
        "minPrice": min(v["price"] for v in variants),
        "available": any(v["available"] for v in variants),
        "productUpdatedAt": prod.get("updated_at") or "",
    }
    # Hand-checked values win and stop being "estimated".
    for key in ("noteType", "longevity", "strength", "profiles", "kind", "form", "cas", "name", "dilution"):
        if key in ov:
            item[key] = ov[key]
            if key in item["estimated"]:
                item["estimated"].remove(key)
    return item


# ── Fetching ───────────────────────────────────────────────────────────────

def fetch_products(base_url: str) -> list[dict]:
    products: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = http_json(f"{base_url}/products.json?limit=250&page={page}")
        batch = data.get("products") or []
        if not batch:
            break
        products.extend(batch)
        print(f"  page {page}: {len(batch)} products", file=sys.stderr)
        time.sleep(PAGE_DELAY_SECONDS)
    return products


def fetch_shop_currency(base_url: str) -> str:
    env = os.environ.get("SUPPLIER_CURRENCY")
    if env:
        return env.upper()
    try:
        meta = http_json(f"{base_url}/meta.json")
        cur = (meta.get("currency") or "").upper()
        if len(cur) == 3:
            return cur
    except RuntimeError as e:
        print(f"  warning: couldn't read shop currency ({e}); assuming USD", file=sys.stderr)
    return "USD"


def fetch_rates(base: str) -> dict | None:
    """Daily reference rates (ECB via frankfurter.app, fallback open.er-api.com)."""
    try:
        data = http_json(f"https://api.frankfurter.app/latest?from={base}")
        rates = dict(data["rates"])
        rates[base] = 1.0
        return {"base": base, "date": data.get("date", ""), "source": "frankfurter.app (ECB)", "rates": rates}
    except (RuntimeError, KeyError) as e:
        print(f"  frankfurter failed: {e}", file=sys.stderr)
    try:
        data = http_json(f"https://open.er-api.com/v6/latest/{base}")
        return {"base": base, "date": data.get("time_last_update_utc", ""), "source": "open.er-api.com",
                "rates": data["rates"]}
    except (RuntimeError, KeyError) as e:
        print(f"  open.er-api failed: {e}", file=sys.stderr)
    return None


# ── Writing ────────────────────────────────────────────────────────────────

def content_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def build_supplier(supplier_id: str, raw_products: list[dict], currency: str) -> dict:
    info = SUPPLIERS[supplier_id]
    overrides = read_json(OVERRIDES_DIR / f"{supplier_id}.json") or {}
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}
    items = []
    for prod in raw_products:
        item = normalise(prod, supplier_id, info["website"], currency, overrides)
        if item:
            items.append(item)
    items.sort(key=lambda i: i["name"].lower())
    return {"schema": SCHEMA, "supplier": supplier_id, "currency": currency, "products": items}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--supplier", default="fraterworks", choices=sorted(SUPPLIERS))
    ap.add_argument("--from-file", help="use a saved products.json instead of fetching")
    ap.add_argument("--currency", help="supplier currency when using --from-file (default USD)")
    ap.add_argument("--skip-rates", action="store_true")
    ap.add_argument("--force", action="store_true", help="publish even if the product count dropped a lot")
    args = ap.parse_args()

    sid = args.supplier
    info = SUPPLIERS[sid]
    print(f"Building {info['name']} catalogue…", file=sys.stderr)

    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        raw_products = raw.get("products", raw) if isinstance(raw, dict) else raw
        currency = (args.currency or "USD").upper()
    else:
        currency = fetch_shop_currency(info["website"])
        raw_products = fetch_products(info["website"])
    print(f"  {len(raw_products)} raw products, currency {currency}", file=sys.stderr)

    data = build_supplier(sid, raw_products, currency)
    new_count = len(data["products"])
    supplier_path = OUT_DIR / f"{sid}.json"
    old = read_json(supplier_path)
    old_count = len(old["products"]) if old else 0

    if new_count == 0:
        print("  ERROR: no products — keeping the old file.", file=sys.stderr)
        return 1
    if old_count and new_count < old_count * (1 - MAX_DROP_FRACTION) and not args.force:
        print(f"  ERROR: product count fell from {old_count} to {new_count} — keeping the old file "
              f"(re-run with --force if this is real).", file=sys.stderr)
        return 1

    version = content_hash(data)
    write_json(supplier_path, data)
    print(f"  wrote {supplier_path.relative_to(ROOT)}: {new_count} products (version {version})", file=sys.stderr)

    manifest_path = OUT_DIR / "manifest.json"
    manifest = read_json(manifest_path) or {"schema": SCHEMA, "suppliers": [], "rates": None}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = next((s for s in manifest["suppliers"] if s["id"] == sid), None)
    if entry is None:
        entry = {"id": sid}
        manifest["suppliers"].append(entry)
    if entry.get("version") != version:
        entry["updatedAt"] = now
    entry.update({
        "name": info["name"], "country": info["country"], "website": info["website"],
        "referralQuery": info["referralQuery"], "file": f"{sid}.json",
        "currency": currency, "count": new_count, "version": version,
    })

    if not args.skip_rates:
        rates = fetch_rates(currency)
        if rates:
            write_json(OUT_DIR / "rates.json", rates)
            manifest["rates"] = {"file": "rates.json", "base": currency, "date": rates["date"],
                                 "version": content_hash(rates)}
        else:
            print("  warning: no exchange rates this run — keeping the previous rates.json", file=sys.stderr)

    manifest["schema"] = SCHEMA
    write_json(manifest_path, manifest)
    print("  wrote catalog/manifest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
