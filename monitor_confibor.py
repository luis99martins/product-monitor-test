import json
import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

SHOP_URL = "https://www.confibor.pt/shop"
STATE_FILE = "confibor_state.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ProductMonitor/1.1; +https://github.com/)"
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


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


def has_sold_out_marker(node: Tag) -> bool:
    """
    Determina se o cartão do produto tem indicador de esgotado.

    Estratégia:
    1. Procurar texto explícito 'esgotado' no bloco do produto.
    2. Procurar classes/atributos comuns de botões desativados/sold out.
    3. Evitar falsos positivos em blocos demasiado grandes.
    """
    text = node.get_text(" ", strip=True).lower()

    # Texto explícito
    if re.search(r"\besgotado\b", text):
        return True

    # Padrões comuns em botões/elementos
    selectors = [
        "[disabled]",
        ".sold-out",
        ".out-of-stock",
        ".product-out-of-stock",
        "[aria-disabled='true']",
    ]
    for sel in selectors:
        if node.select_one(sel):
            return True

    return False


def find_product_container(a: Tag) -> Tag:
    """
    Sobe na árvore DOM até encontrar um contentor razoável do produto.
    Em vez de subir um número fixo de níveis, escolhe o primeiro ancestral
    que contenha o link do produto e cujo texto não seja demasiado grande.
    """
    current = a

    for _ in range(10):
        parent = current.parent
        if not isinstance(parent, Tag):
            break

        parent_text = parent.get_text(" ", strip=True)
        product_links = parent.find_all("a", href=True)

        same_product_links = [
            link for link in product_links
            if "/shop/p/" in link.get("href", "")
        ]

        # Heurística:
        # - deve conter pelo menos este produto
        # - texto não deve ser gigante (evita apanhar a grelha toda)
        if same_product_links and len(parent_text) < 500:
            current = parent
            break

        current = parent

    return current


def fetch_confibor_products() -> dict:
    response = requests.get(SHOP_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    products = {}

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if "/shop/p/" not in href:
            continue

        url = normalize_url(urljoin(SHOP_URL, href))

        # Evitar duplicados do mesmo produto
        if url in products:
            continue

        name = a.get("title", "").strip() or a.get_text(" ", strip=True)
        if not name:
            name = slug_to_name(url)

        container = find_product_container(a)
        sold_out = has_sold_out_marker(container)

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

    # 1) Novo produto
    for url, item in new.items():
        if url not in old:
            alerts.append(
                f"🆕 Novo produto na Confibor: **{item['name']}**\n{url}"
            )

    # 2) Produto estava esgotado e deixou de estar
    for url, new_item in new.items():
        old_item = old.get(url)
        if not old_item:
            continue

        if old_item.get("sold_out") is True and new_item.get("sold_out") is False:
            alerts.append(
                f"✅ Produto voltou a estar disponível na Confibor: **{new_item['name']}**\n{url}"
            )

    return alerts


def main() -> None:
    old_state = load_state()
    new_state = fetch_confibor_products()

    alerts = build_alerts(old_state, new_state)

    if not old_state:
        print("Primeira execução: estado inicial guardado. Sem alertas.")
    else:
        if alerts:
            for message in alerts:
                print(message)
                send_discord(message)
        else:
            print("Sem novidades.")

    save_state(new_state)


if __name__ == "__main__":
    main()
