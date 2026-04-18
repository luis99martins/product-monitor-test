import argparse
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

SHOP_URL = "https://www.confibor.pt/shop"
DEFAULT_STATE_FILE = "confibor_state.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ProductMonitor/1.1; +https://github.com/)"
}

# Cores para Discord embeds
COLOR_NEW_PRODUCT = 3447003  # Azul
COLOR_BACK_IN_STOCK = 5763719  # Verde
COLOR_ERROR = 15158332  # Vermelho


def normalize_url(url: str) -> str:
    """Normaliza URL removendo parâmetros e trailing slashes."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def load_state(state_file: str) -> dict:
    """Carrega o estado anterior dos produtos."""
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            # Compatibilidade: se tiver timestamp, retornar só os produtos
            return state.get("products", state)
    except Exception as e:
        print(f"⚠️ Erro ao carregar estado: {e}")
        return {}


def save_state(data: dict, state_file: str) -> None:
    """Guarda o estado atual dos produtos com timestamp."""
    state = {
        "last_check": datetime.now().isoformat(),
        "total_products": len(data),
        "products": data
    }
    
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def slug_to_name(url: str) -> str:
    """Converte slug da URL em nome legível."""
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
    """Faz scraping da loja Confibor e retorna dicionário com produtos."""
    try:
        print(f"🌐 A aceder a {SHOP_URL}...")
        response = requests.get(SHOP_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ Erro ao aceder à loja: {e}")
        # Em caso de erro, retornar vazio (não sobrescrever estado)
        return {}

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

    print(f"✅ Encontrados {len(products)} produtos")
    return products


def send_discord_embed(title: str, description: str, url: str, color: int) -> None:
    """Envia notificação para Discord usando embed formatado."""
    if not WEBHOOK_URL:
        print("⚠️ DISCORD_WEBHOOK_URL não configurado; a saltar alerta Discord.")
        return

    embed = {
        "title": title,
        "description": description,
        "url": url,
        "color": color,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        r = requests.post(
            WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=30
        )
        r.raise_for_status()
        print(f"✉️ Notificação enviada: {title}")
    except requests.RequestException as e:
        print(f"❌ Erro ao enviar para Discord: {e}")


def build_alerts(old: dict, new: dict) -> list[dict]:
    """Compara estados e gera lista de alertas."""
    alerts = []

    # 1) Novo produto
    for url, item in new.items():
        if url not in old:
            alerts.append({
                "title": "🆕 Novo produto na Confibor",
                "description": item['name'],
                "url": url,
                "color": COLOR_NEW_PRODUCT
            })

    # 2) Produto estava esgotado e deixou de estar
    for url, new_item in new.items():
        old_item = old.get(url)
        if not old_item:
            continue

        if old_item.get("sold_out") is True and new_item.get("sold_out") is False:
            alerts.append({
                "title": "✅ Produto voltou ao stock",
                "description": new_item['name'],
                "url": url,
                "color": COLOR_BACK_IN_STOCK
            })

    return alerts


def main(state_file: str = DEFAULT_STATE_FILE) -> None:
    """Função principal do monitor."""
    print("=" * 60)
    print(f"🔍 Monitor Confibor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    old_state = load_state(state_file)
    print(f"📦 Estado anterior: {len(old_state)} produtos")

    new_state = fetch_confibor_products()

    # Se falhou o scraping, não sobrescrever o estado
    if not new_state and old_state:
        print("⚠️ Scraping retornou vazio. A manter estado anterior.")
        print("   (Possível problema de rede ou mudança no site)")
        return

    print(f"📦 Estado novo: {len(new_state)} produtos")

    alerts = build_alerts(old_state, new_state)

    if not old_state:
        print("\n🎯 Primeira execução: estado inicial guardado.")
        print("   A partir da próxima execução, serão detetadas alterações.")
    else:
        if alerts:
            print(f"\n🔔 {len(alerts)} alerta(s) encontrado(s):")
            for alert in alerts:
                print(f"   • {alert['title']}: {alert['description']}")
                send_discord_embed(
                    title=alert['title'],
                    description=alert['description'],
                    url=alert['url'],
                    color=alert['color']
                )
        else:
            print("\n😴 Sem novidades.")

    save_state(new_state, state_file)
    print(f"\n💾 Estado guardado em {state_file}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Monitor da loja Confibor para detetar novos produtos e reposições de stock"
    )
    parser.add_argument(
        "--state-file",
        type=str,
        default=DEFAULT_STATE_FILE,
        help=f"Caminho para o ficheiro de estado (default: {DEFAULT_STATE_FILE})"
    )
    
    args = parser.parse_args()
    
    try:
        main(state_file=args.state_file)
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrompido pelo utilizador")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}")
        sys.exit(1)
