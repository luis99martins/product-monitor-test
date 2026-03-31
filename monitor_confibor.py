import json
import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

SHOP_URL = "https://www.confibor.pt/shop"
STATE_FILE = "confibor_state.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ProductMonitor/1.0; +https://github.com/)"
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))



def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}



def save_state(data: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)



def slug_to_name(url: str) -> str:
    slug = normalize_url(url).split("/")[-1]
    slug = re.sub(r"[-_]+", " ", slug)
    return slug.strip() or url



def fetch_confibor_products() -> dict:
    response = requests.get(SHOP_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    products = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/shop/p/" not in href:
            continue

        url = normalize_url(urljoin(SHOP_URL, href))

        name = a.get("title", "").strip() or a.get_text(" ", strip=True)
        if not name:
            name = slug_to_name(url)

        container = a
        for _ in range(5):
            if container.parent is None:
                break
            container = container.parent
        card_text = container.get_text(" ", strip=True).lower()
        sold_out = "esgotado" in card_text

        products[url] = {
            "name": name,
            "url": url,
            "sold_out": sold_out,
        }

    return products



def send_discord(message: str) -> None:
    if not WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not configured; skipping Discord alert.")
        return

    r = requests.post(WEBHOOK_URL, json={"content": message}, timeout=30)
    r.raise_for_status()



def build_alerts(old: dict, new: dict) -> list[str]:
    alerts = []

    for url, item in new.items():
        if url not in old:
            alerts.append(f"🆕 Novo produto na Confibor: **{item['name']}**\n{url}")

    for url, old_item in old.items():
        if url not in new:
            continue
        new_item = new[url]
        if old_item.get("sold_out") is True and new_item.get("sold_out") is False:
            alerts.append(f"✅ Produto voltou a estar disponível na Confibor: **{new_item['name']}**\n{url}")

    return alerts



def main() -> None:
    old_state = load_state()
    new_state = fetch_confibor_products()

    alerts = build_alerts(old_state, new_state)

    if not old_state:
        print("Primeira execução: estado inicial guardado. Sem alertas.")
    else:
        for message in alerts:
            print(message)
            send_discord(message)
        if not alerts:
            print("Sem novidades.")

    save_state(new_state)


if __name__ == "__main__":
    main()
