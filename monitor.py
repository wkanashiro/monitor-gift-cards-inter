"""Daily monitor for Inter Shopping gift card cashback offers with Telegram alerts."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_URL = (
    "https://marketplace-api.web.bancointer.com.br/"
    "site/giftcard/inter/v1/giftcards/search?lang=pt-BR&category="
)
SITE_GIFT_CARD_BASE = "https://shopping.inter.co/gift-card"
TELEGRAM_MAX_MESSAGE_CHARS = 4000
VALID_NOTIFY_MODES = ("above_threshold", "on_change")


@dataclass(frozen=True)
class MatchedOffer:
    """Gift card offer that matched a watched brand and threshold rule."""

    card_id: str
    name: str
    slug: str
    benefits: str
    cashback: float
    threshold: float
    brand_label: str
    previous_cashback: float | None = None

    @property
    def url(self) -> str:
        """Return the public Inter Shopping URL for this gift card."""
        return f"{SITE_GIFT_CARD_BASE}/{self.slug}"


def normalize_text(text: str) -> str:
    """Lowercase text and strip diacritics for accent-insensitive comparison."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Check whether phrase appears as whole tokens inside normalized_text."""
    normalized_phrase = normalize_text(phrase).strip()
    if not normalized_phrase:
        return False
    pattern = rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)"
    return re.search(pattern, normalized_text) is not None


def find_matching_brand(
    card_name: str, brands: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the first watched brand configuration matching card_name, if any."""
    normalized_name = normalize_text(card_name)
    for brand in brands:
        excluded = brand.get("exclude_keywords", [])
        if any(contains_phrase(normalized_name, term) for term in excluded):
            continue
        keywords = brand.get("keywords", [])
        if any(contains_phrase(normalized_name, kw) for kw in keywords):
            return brand
    return None


def fetch_gift_cards(api_url: str = API_URL, timeout: int = 20) -> list[dict[str, Any]]:
    """Fetch the full gift card catalog from the Inter Shopping public API."""
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; monitor-gift-cards-inter/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    gift_cards = payload.get("giftCards")
    if not isinstance(gift_cards, list):
        raise ValueError("Resposta inesperada da API: campo 'giftCards' ausente ou inválido.")
    return gift_cards


def load_config(config_path: Path) -> dict[str, Any]:
    """Load monitor configuration from JSON file."""
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    if not isinstance(config.get("brands"), list):
        raise ValueError("Arquivo de configuração inválido: 'brands' deve ser uma lista.")
    return config


def load_state(state_path: Path) -> dict[str, float]:
    """Load previous cashback values keyed by gift card ID."""
    if not state_path.exists():
        return {}
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items() if v is not None}


def save_state(state_path: Path, state: dict[str, float]) -> None:
    """Persist cashback state to disk."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def format_percent(value: float) -> str:
    """Format numeric cashback percentage without trailing .0 when integer."""
    return f"{value:g}%"


def evaluate_offers(
    cards: list[dict[str, Any]],
    brands: list[dict[str, Any]],
    default_threshold: float,
    notify_mode: str = "above_threshold",
    previous_state: dict[str, float] | None = None,
) -> tuple[list[MatchedOffer], dict[str, float]]:
    """Filter gift cards by watched brands and threshold, returning alerts and new state."""
    if notify_mode not in VALID_NOTIFY_MODES:
        raise ValueError(f"notify_mode inválido: {notify_mode}. Use {VALID_NOTIFY_MODES}.")

    prev = previous_state or {}
    alerts: list[MatchedOffer] = []
    new_state: dict[str, float] = {}

    for card in cards:
        name = str(card.get("name") or "")
        brand = find_matching_brand(name, brands)
        if brand is None:
            continue

        card_id = str(card["id"])
        cashback = float(card.get("cashbackValue") or 0)
        threshold = float(brand.get("min_cashback", default_threshold))
        old_cashback = prev.get(card_id)
        new_state[card_id] = cashback

        if cashback <= threshold:
            continue

        if notify_mode == "on_change" and old_cashback is not None and cashback <= old_cashback:
            continue

        alerts.append(
            MatchedOffer(
                card_id=card_id,
                name=name,
                slug=str(card.get("slug") or ""),
                benefits=str(card.get("benefits") or f"{format_percent(cashback)} de cashback"),
                cashback=cashback,
                threshold=threshold,
                brand_label=str(brand.get("label") or name),
                previous_cashback=old_cashback,
            )
        )

    alerts.sort(key=lambda item: (-item.cashback, item.name.casefold()))
    return alerts, new_state


def format_offer_line(offer: MatchedOffer) -> str:
    """Format a single MatchedOffer as a Telegram HTML bullet item."""
    safe_name = html.escape(offer.name)
    safe_benefits = html.escape(offer.benefits)
    safe_url = html.escape(offer.url, quote=True)

    delta_note = ""
    if offer.previous_cashback is not None and offer.cashback > offer.previous_cashback:
        delta_note = f" (antes: {format_percent(offer.previous_cashback)})"
    elif offer.previous_cashback is None and offer.benefits:
        delta_note = ""

    return f'• <a href="{safe_url}">{safe_name}</a> — <b>{safe_benefits}</b>{delta_note}'


def build_telegram_messages(
    offers: list[MatchedOffer],
    default_threshold: float,
    max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS,
) -> list[str]:
    """Build one or more Telegram HTML messages respecting Telegram's character limit."""
    if not offers:
        return []

    header = (
        "<b>🔔 Ofertas de Gift Card - Inter Shop</b>\n"
        f"Cashback acima de <b>{html.escape(format_percent(default_threshold))}</b>:\n\n"
    )
    lines = [format_offer_line(offer) for offer in offers]

    messages: list[str] = []
    current = header
    for line in lines:
        addition = line if current == header or current == "" else f"\n{line}"
        if len(current) + len(addition) > max_chars:
            messages.append(current)
            current = f"<b>🔔 Ofertas (continuação)</b>\n\n{line}"
        else:
            current += addition

    if current:
        messages.append(current)
    return messages


def send_telegram_messages(
    messages: list[str], bot_token: str, chat_id: str, timeout: int = 20
) -> None:
    """Send HTML-formatted messages via Telegram Bot API."""
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for text in messages:
        body = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Erro ao enviar para o Telegram ({exc.code}): {detail}") from exc

        if not result.get("ok"):
            raise RuntimeError(f"Telegram API retornou erro: {result}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Monitora ofertas de gift cards com cashback no Shopping do Inter."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "config.json",
        help="Caminho para o arquivo config.json.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(__file__).parent / "state.json",
        help="Caminho para o arquivo state.json (usado no modo on_change).",
    )
    parser.add_argument(
        "--min-cashback",
        type=float,
        default=None,
        help="Sobrescreve o limiar mínimo de cashback (%%).",
    )
    parser.add_argument(
        "--mode",
        choices=VALID_NOTIFY_MODES,
        default=None,
        help="Sobrescreve o modo de notificação (above_threshold ou on_change).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Executa a consulta e imprime o alerta sem enviar ao Telegram.",
    )
    return parser.parse_args(argv)


def resolve_threshold(cli_value: float | None, config: dict[str, Any]) -> float:
    """Resolve global threshold from CLI arg > MIN_CASHBACK env > config.json."""
    if cli_value is not None:
        return cli_value
    env_val = os.environ.get("MIN_CASHBACK", "").strip()
    if env_val:
        return float(env_val)
    return float(config.get("min_cashback_percent", 3))


def resolve_notify_mode(cli_mode: str | None, config: dict[str, Any]) -> str:
    """Resolve notification mode from CLI arg > NOTIFY_MODE env > config.json."""
    if cli_mode is not None:
        return cli_mode
    env_mode = os.environ.get("NOTIFY_MODE", "").strip()
    if env_mode:
        return env_mode
    return str(config.get("notify_mode", "above_threshold"))


def main(argv: list[str] | None = None) -> int:
    """Run the gift card cashback monitor."""
    args = parse_args(argv)
    config = load_config(args.config)
    threshold = resolve_threshold(args.min_cashback, config)
    notify_mode = resolve_notify_mode(args.mode, config)
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").strip().lower() in {"1", "true", "yes"}

    previous_state = load_state(args.state_file) if notify_mode == "on_change" else {}
    cards = fetch_gift_cards()
    alerts, new_state = evaluate_offers(
        cards=cards,
        brands=config["brands"],
        default_threshold=threshold,
        notify_mode=notify_mode,
        previous_state=previous_state,
    )

    print(
        f"Consultados {len(cards)} gift cards | "
        f"Monitorados: {len(new_state)} | "
        f"Alertas (> {format_percent(threshold)}): {len(alerts)} | "
        f"Modo: {notify_mode}"
    )

    if notify_mode == "on_change" and not dry_run:
        save_state(args.state_file, new_state)

    if not alerts:
        print("Nenhuma oferta acima do limiar encontrado nesta execução.")
        return 0

    messages = build_telegram_messages(alerts, default_threshold=threshold)
    if dry_run:
        print("\n--- DRY RUN (Telegram) ---")
        for msg in messages:
            print(msg)
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print(
            "Erro: defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID para enviar alertas.",
            file=sys.stderr,
        )
        return 1

    send_telegram_messages(messages, bot_token=bot_token, chat_id=chat_id)
    print(f"Enviadas {len(messages)} mensagem(ns) para o Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
