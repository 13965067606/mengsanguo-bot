import json
import logging
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import main


ROOT = Path(__file__).resolve().parent.parent


def frame_with_health(fill: float) -> Image.Image:
    image = Image.new("RGB", (1024, 768), (12, 12, 12))
    draw = ImageDraw.Draw(image)
    x, y, width, height = 54, 31, 92, 17
    usable = width - 7
    pixels = max(0, min(usable, round(usable * fill)))
    if pixels:
        draw.rectangle((x + 5, y + 3, x + 5 + pixels - 1, y + height - 4), fill=(35, 180, 55))
    return image


class Combat52Tests(unittest.TestCase):
    def make_engine(self):
        engine = main.BotEngine.__new__(main.BotEngine)
        engine.config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        engine.log = logging.getLogger("test-5.2")
        engine.retreat_latched = False
        engine.retreat_until = 0.0
        engine.retreat_recovery_samples = 0
        engine.low_health_item_uses = 0
        engine.last_health_ratio = None
        engine.respawn_health_samples = 0
        engine.last_inventory_use = 0.0
        engine.no_enemy_since = 0.0
        engine.last_combat_seen = 0.0
        engine.state = ""
        engine._use_counted_inventory_items = lambda _image, limit=None: 0
        engine._retreat_to_team_or_home = lambda _image, _now, prefer_home=False: True
        return engine

    def test_missing_personal_config_is_seeded_from_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            defaults = {"window": {"title": "梦三国2"}, "timing": {"scan_interval": 0.5}}
            (base / "config.example.json").write_text(
                json.dumps(defaults, ensure_ascii=False), encoding="utf-8"
            )
            app = main.App.__new__(main.App)
            app.base = base

            self.assertEqual(app.load_config(), defaults)
            self.assertEqual(
                json.loads((base / "config.json").read_text(encoding="utf-8")), defaults
            )

    def test_health_bar_ratio(self):
        engine = self.make_engine()
        self.assertAlmostEqual(engine._self_health_ratio(frame_with_health(1.0)), 1.0, delta=0.03)
        self.assertAlmostEqual(engine._self_health_ratio(frame_with_health(0.20)), 0.20, delta=0.04)

    def test_retreat_stays_latched_until_hold_and_recovery(self):
        engine = self.make_engine()
        low = frame_with_health(0.20)
        high = frame_with_health(0.80)
        self.assertTrue(engine._handle_low_health_retreat(low, 100.0))
        self.assertTrue(engine.retreat_latched)
        self.assertTrue(engine._handle_low_health_retreat(high, 105.0))
        self.assertTrue(engine._handle_low_health_retreat(high, 109.0))
        self.assertFalse(engine._handle_low_health_retreat(high, 110.1))
        self.assertFalse(engine.retreat_latched)

    def test_low_health_items_are_limited_per_retreat(self):
        engine = self.make_engine()
        uses = []
        engine._use_counted_inventory_items = lambda _image, limit=None: uses.append(limit) or 1
        low = frame_with_health(0.20)
        for index in range(6):
            engine.last_inventory_use = 0.0
            self.assertTrue(engine._handle_low_health_retreat(low, 100.0 + index * 6.0))
        self.assertEqual(uses, [1, 1])
        self.assertEqual(engine.low_health_item_uses, 2)

    def test_low_health_does_not_retreat_when_teammates_are_nearby(self):
        engine = self.make_engine()
        engine.last_target_friendly_count = 3
        low = frame_with_health(0.12)
        self.assertFalse(engine._handle_low_health_retreat(low, 100.0, teammates_nearby=True))
        self.assertFalse(engine.retreat_latched)

    def test_teammates_cancel_an_existing_low_health_retreat(self):
        engine = self.make_engine()
        low = frame_with_health(0.12)
        self.assertTrue(engine._handle_low_health_retreat(low, 100.0))
        engine.last_target_friendly_count = 2
        self.assertFalse(engine._handle_low_health_retreat(low, 101.0, teammates_nearby=True))
        self.assertFalse(engine.retreat_latched)

    def test_respawn_requires_three_healthy_samples(self):
        engine = self.make_engine()
        high = frame_with_health(0.80)
        low = frame_with_health(0.10)
        self.assertFalse(engine._respawn_health_confirmed(high))
        self.assertFalse(engine._respawn_health_confirmed(high))
        self.assertFalse(engine._respawn_health_confirmed(low))
        self.assertFalse(engine._respawn_health_confirmed(high))
        self.assertFalse(engine._respawn_health_confirmed(high))
        self.assertTrue(engine._respawn_health_confirmed(high))

    def test_recent_team_order_allows_retarget_after_settle(self):
        engine = self.make_engine()
        engine.last_group_follow = 100.0
        engine.last_group_point = [100, 650]
        engine._find_minimap_friendly_group = lambda _image: None
        self.assertFalse(engine._follow_friendly_group(Image.new("RGB", (1024, 768)), 107.0))

    def test_stable_team_fight_does_not_force_reentry(self):
        engine = self.make_engine()
        engine.last_group_follow = 100.0
        engine.last_group_point = [100, 650]
        engine._find_minimap_friendly_group = lambda _image: ([105, 654], 65, 24)
        moves = []
        engine._move_to_tactical_point = lambda point, label: moves.append((point, label)) or True
        self.assertFalse(engine._follow_friendly_group(Image.new("RGB", (1024, 768)), 120.0))
        self.assertEqual(moves, [])

    def test_persisted_58_objective_flag_cannot_reenable_false_lock(self):
        engine = self.make_engine()
        engine.config["combat"]["objective_support_enabled"] = True
        engine.last_group_follow = 100.0
        engine.last_group_point = [100, 650]
        engine._find_minimap_friendly_group = lambda _image: ([105, 654], 81, 46)
        moves = []
        engine._move_to_tactical_point = lambda point, label: moves.append((point, label)) or True
        self.assertFalse(engine._follow_friendly_group(Image.new("RGB", (1024, 768)), 120.0))
        self.assertEqual(moves, [])

    def test_bottom_edge_icons_are_not_objective_support(self):
        engine = self.make_engine()
        engine.last_group_follow = 0.0
        engine.last_group_point = None
        engine._find_minimap_friendly_group = lambda _image: ([69, 741], 92, 25)
        moves = []
        engine._move_to_tactical_point = lambda point, label: moves.append((point, label)) or True
        self.assertTrue(engine._follow_friendly_group(Image.new("RGB", (1024, 768)), 120.0))
        self.assertNotIn("团队目标", moves[0][1])

    def test_bottom_right_fountain_is_not_a_friendly_group(self):
        engine = self.make_engine()
        enemy = __import__("numpy").zeros((154, 158), dtype=bool)
        friendly = enemy.copy()
        friendly[118:142, 122:150] = True
        enemy[122:137, 128:148] = True
        engine._minimap_color_masks = lambda _image: (enemy, friendly)
        self.assertIsNone(engine._find_minimap_friendly_group(Image.new("RGB", (1024, 768))))

    def test_central_contested_group_remains_available(self):
        engine = self.make_engine()
        enemy = __import__("numpy").zeros((154, 158), dtype=bool)
        friendly = enemy.copy()
        friendly[68:76, 76:88] = True
        enemy[72:80, 84:94] = True
        engine._minimap_color_masks = lambda _image: (enemy, friendly)
        group = engine._find_minimap_friendly_group(Image.new("RGB", (1024, 768)))
        self.assertIsNotNone(group)
        self.assertGreaterEqual(group[2], 8)

    def test_camera_map_move_immediately_restores_self_view(self):
        engine = self.make_engine()
        engine.config["combat"]["minimap_move_mode"] = "camera"
        engine._safe_click_point = lambda *_args, **_kwargs: True
        engine._safe_click_ratio = lambda *_args, **_kwargs: True
        restores = []
        engine._restore_self_view = lambda reason: restores.append(reason) or True
        self.assertTrue(engine._move_to_tactical_point([80, 680], "测试支援点"))
        self.assertEqual(restores, ["战术移动已下达，立即返回自身视角"])

    def test_restore_self_view_updates_selection_and_timestamp(self):
        engine = self.make_engine()
        engine.self_selected = False
        engine.last_self_focus = 0.0
        engine._focus_self = lambda _reason: True
        self.assertTrue(engine._restore_self_view("测试"))
        self.assertTrue(engine.self_selected)
        self.assertGreater(engine.last_self_focus, 0.0)


if __name__ == "__main__":
    unittest.main()
