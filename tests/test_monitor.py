"""Unit tests for the Inter gift card cashback monitor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import monitor

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


SAMPLE_CARDS = [
    {"id": 1636, "slug": "uber-1636", "name": "Gift Card Uber", "benefits": "3% de cashback", "cashbackValue": 3},
    {"id": 172, "slug": "pague-menos-172", "name": "Gift Card Pague Menos", "benefits": "7% de cashback", "cashbackValue": 7},
    {"id": 3646, "slug": "cupom-petz-3646", "name": "Gift Card Cupom Petz", "benefits": "3% de cashback", "cashbackValue": 3},
    {"id": 1666, "slug": "petlove-1666", "name": "Gift Card Petlove", "benefits": "8% de cashback", "cashbackValue": 8},
    {"id": 1595, "slug": "google-play-1595", "name": "Gift Card Google Play", "benefits": "4% de cashback", "cashbackValue": 4},
    {"id": 1627, "slug": "app-store-1627", "name": "Gift Card App Store", "benefits": "4% de cashback", "cashbackValue": 4},
    {"id": 1737, "slug": "outback-steakhouse-1737", "name": "Gift Card Outback Steakhouse", "benefits": "10% de cashback", "cashbackValue": 10},
    {"id": 4530, "slug": "outback-golden-card-4530", "name": "Gift Card Outback Golden Card", "benefits": "10% de cashback", "cashbackValue": 10},
    {"id": 1389, "slug": "assai-1389", "name": "Gift Card Assaí", "benefits": "3% de cashback", "cashbackValue": 3},
    {"id": 1958, "slug": "carrefour-1958", "name": "Gift Card Carrefour", "benefits": "2% de cashback", "cashbackValue": 2},
    {"id": 4227, "slug": "carrefour-drogaria-4227", "name": "Gift Card Carrefour Drogaria", "benefits": "2% de cashback", "cashbackValue": 2},
    {"id": 4226, "slug": "atacadao-4226", "name": "Gift Card Atacadão", "benefits": "2% de cashback", "cashbackValue": 2},
    {"id": 1956, "slug": "cupom-shopee-1956", "name": "Gift Card Cupom Shopee", "benefits": "2% de cashback", "cashbackValue": 2},
    {"id": 4162, "slug": "nc-soft-4162", "name": "Gift Card NC Soft", "benefits": "40% de cashback", "cashbackValue": 40},
]


class MonitorTests(unittest.TestCase):
    """Tests for brand matching, threshold evaluation, and message building."""

    def setUp(self) -> None:
        self.config = monitor.load_config(CONFIG_PATH)

    def test_brand_matching_covers_all_requested_items_and_avoids_false_positives(self) -> None:
        matched_ids = {
            card["id"]
            for card in SAMPLE_CARDS
            if monitor.find_matching_brand(card["name"], self.config["brands"]) is not None
        }
        expected_ids = {1636, 172, 3646, 1595, 1627, 1737, 4530, 1389, 1958, 4226, 1956}
        self.assertEqual(matched_ids, expected_ids)
        self.assertNotIn(4227, matched_ids)  # Carrefour Drogaria excluded
        self.assertNotIn(1666, matched_ids)  # Petlove must not match Petz
        self.assertNotIn(4162, matched_ids)  # NC Soft is not in watched list

    def test_strict_threshold_above_3_percent(self) -> None:
        alerts, state = monitor.evaluate_offers(
            cards=SAMPLE_CARDS,
            brands=self.config["brands"],
            default_threshold=3,
            notify_mode="above_threshold",
        )
        self.assertEqual(len(state), 11)
        alerted_ids = [offer.card_id for offer in alerts]
        # 10% (Outback x2), 7% (Pague Menos), 4% (App Store, Google Play) — Uber/Petz/Assaí at 3% excluded
        self.assertEqual(alerted_ids, ["4530", "1737", "172", "1627", "1595"])

    def test_fractional_cashback_above_threshold_triggers(self) -> None:
        cards = [
            {"id": 1636, "slug": "uber-1636", "name": "Gift Card Uber", "benefits": "3.5% de cashback", "cashbackValue": 3.5}
        ]
        alerts, _ = monitor.evaluate_offers(cards, self.config["brands"], default_threshold=3)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].cashback, 3.5)

    def test_per_brand_threshold_override(self) -> None:
        brands = [
            {"label": "Outback", "keywords": ["outback"], "min_cashback": 10},
            {"label": "Pague Menos", "keywords": ["pague menos"]},
        ]
        alerts, _ = monitor.evaluate_offers(SAMPLE_CARDS, brands, default_threshold=3)
        self.assertEqual([a.card_id for a in alerts], ["172"])

    def test_on_change_mode_alerts_only_when_new_or_increased(self) -> None:
        prev_state = {"1737": 10.0, "172": 5.0, "1595": 4.0}
        alerts, new_state = monitor.evaluate_offers(
            cards=SAMPLE_CARDS,
            brands=self.config["brands"],
            default_threshold=3,
            notify_mode="on_change",
            previous_state=prev_state,
        )
        # 1737 unchanged (10->10), 1595 unchanged (4->4)
        # 4530 new (None->10), 172 increased (5->7), 1627 new (None->4)
        self.assertEqual([a.card_id for a in alerts], ["4530", "172", "1627"])
        self.assertEqual(new_state["172"], 7.0)

    def test_telegram_message_escaping_and_splitting(self) -> None:
        offers = [
            monitor.MatchedOffer(
                card_id=str(i),
                name=f"Gift Card Marca & Co {i}",
                slug=f"marca-{i}",
                benefits="5% de cashback",
                cashback=5.0,
                threshold=3.0,
                brand_label="Marca",
            )
            for i in range(15)
        ]
        messages = monitor.build_telegram_messages(offers, default_threshold=3, max_chars=300)
        self.assertGreater(len(messages), 1)
        for msg in messages:
            self.assertLessEqual(len(msg), 300)
            self.assertIn("&amp;", msg)

    def test_state_persistence_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            self.assertEqual(monitor.load_state(state_file), {})
            monitor.save_state(state_file, {"1636": 5.0})
            self.assertEqual(monitor.load_state(state_file), {"1636": 5.0})
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(raw, {"1636": 5.0})


if __name__ == "__main__":
    unittest.main()
