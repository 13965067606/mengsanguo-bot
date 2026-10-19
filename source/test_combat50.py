"""Offline tests with fake input devices; never controls a game window."""
import json
import logging
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
import main as bot


class Combat50Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=json.loads((Path(__file__).parent.parent/'config.example.json').read_text(encoding='utf8'))
        self.engine=bot.BotEngine(Path(self.temp.name),self.config,logging.getLogger('combat50'))
        self.engine.self_selected=True
        self.engine.last_self_focus=time.monotonic()
        self.config['entry']['talent_choice']=2
        self.config['combat']['use_counted_inventory_items']=False
        self.engine.last_target_friendly_count=3
        self.keys=[];self.clicks=[]
        self.engine.window=SimpleNamespace(key_tap=lambda key:self.keys.append(key) or True,
                                           grab=lambda:(None,self.frame()))
        self.engine._safe_click_point=lambda point,*args:self.clicks.append((point,args)) or True
        self.engine._handle_boss_or_tower_priority=lambda *args:False

    def tearDown(self): self.temp.cleanup()

    def frame(self,ratio=1,color='green'):
        im=Image.new('RGB',(1024,768))
        if ratio>0:
            ImageDraw.Draw(im).rectangle((59,38,58+max(1,int(85*ratio)),43),fill={'green':(20,180,25),'red':(180,20,20),'yellow':(160,130,15)}[color])
        return im

    def test_health_red_yellow_green(self):
        for value,color in ((1,'green'),(.65,'green'),(.15,'red'),(.35,'yellow')):
            with self.subTest(value=value):self.assertAlmostEqual(self.engine._self_health_ratio(self.frame(value,color)),value,delta=.025)

    def test_unreadable_health_is_unknown(self):
        self.assertIsNone(self.engine._self_health_ratio(Image.new('RGB',(1024,768))))
        self.assertIsNone(self.engine._self_health_ratio(Image.new('RGB',(1920,1080))))

    def test_low_health_retreats_instead_of_attacking(self):
        e=self.engine; e.last_target_friendly_count=0; e._find_enemy_target=lambda im:[500,350]
        retreats=[];e._retreat_to_team_or_home=lambda *args:retreats.append(True)
        e._fight(self.frame(.15,'red'))
        self.assertTrue(retreats);self.assertEqual(self.keys,[]);self.assertEqual(self.clicks,[])

    def test_low_health_keeps_fighting_with_teammates(self):
        e=self.engine; e._find_enemy_target=lambda im:[500,350]
        retreats=[];e._retreat_to_team_or_home=lambda *args:retreats.append(True)
        e._fight(self.frame(.15,'red'))
        self.assertFalse(retreats);self.assertIn('a',self.keys);self.assertIn('q',self.keys)

    def test_healthy_combat_preserved_and_reselects_self(self):
        e=self.engine;e._find_enemy_target=lambda im:[500,350]
        e._fight(self.frame())
        self.assertIn('a',self.keys);self.assertIn('q',self.keys)
        self.assertEqual(self.keys[-1],'f1')
        self.assertGreater(e.combat_order_hold_until,time.monotonic())

    def test_windup_does_not_send_orders(self):
        self.engine.combat_order_hold_until=time.monotonic()+1
        self.engine._fight(self.frame());self.assertEqual(self.keys,[]);self.assertEqual(self.clicks,[])

    def prepare_card(self):
        e=self.engine; now=time.monotonic()
        self.config['entry']['card_flip_count']=3
        e.no_enemy_since=now-20;e.last_combat_seen=now-20;e.last_group_follow=now-20
        e._choose_next_card_stack=lambda im:[995,305]
        return e,now

    def test_recent_combat_blocks_cards(self):
        e,now=self.prepare_card();e.last_combat_seen=now-2
        self.assertFalse(e._try_open_cards(self.frame(),now));self.assertEqual(self.clicks,[])

    def test_single_missing_target_frame_blocks_cards(self):
        e,now=self.prepare_card();e.no_enemy_since=now-1
        self.assertFalse(e._try_open_cards(self.frame(),now));self.assertEqual(self.clicks,[])

    def test_low_health_blocks_cards(self):
        e,now=self.prepare_card()
        self.assertFalse(e._try_open_cards(self.frame(.3,'yellow'),now))

    def test_safe_card_still_opens(self):
        e,now=self.prepare_card();self.assertTrue(e._try_open_cards(self.frame(),now))
        self.assertEqual(self.clicks[0][0],[995,305])

    def test_score_cap_still_blocks_cards(self):
        e,now=self.prepare_card();e.score_cap_reached=True
        self.assertFalse(e._try_open_cards(self.frame(),now))

    def test_auto_purchase_has_no_frame(self):
        im=self.frame();ImageDraw.Draw(im).text((975,412),'17000',fill='white')
        self.assertFalse(self.engine._card_stack_frame_visible(im,412))
        self.assertFalse(self.engine._card_stack_frame_visible(im,443))

    def test_card_frame_and_hover_frame(self):
        for color in ((90,78,62),(20,180,190)):
            im=self.frame();d=ImageDraw.Draw(im)
            d.line((975,287,975,324),fill=color,width=2)
            d.line((1018,287,1018,324),fill=color,width=2)
            self.assertTrue(self.engine._card_stack_frame_visible(im,305))

    def test_retreat_group_avoids_enemy_cluster(self):
        e=self.engine;enemy=np.zeros((154,158),bool);friendly=enemy.copy()
        friendly[65:70,50:55]=True
        friendly[90:96,95:101]=True;enemy[90:96,103:109]=True
        e._minimap_color_masks=lambda im:(enemy,friendly)
        safe=e._find_minimap_friendly_group(self.frame(),safe=True)
        attack=e._find_minimap_friendly_group(self.frame())
        self.assertIsNotNone(safe);self.assertLessEqual(safe[2],3)
        self.assertIsNotNone(attack);self.assertGreaterEqual(attack[2],8)

    def test_panel_not_flipped_in_combat(self):
        e=self.engine
        self.config['entry']['card_flip_count']=3
        e._card_panel_button=lambda im:None;e._card_one_click_button=lambda im:[650,570]
        e._sample_battle_stats=lambda *args,**kwargs:None
        e._find_enemy_target=lambda im:[500,350]
        self.assertTrue(e._handle_card_panel(self.frame()))
        self.assertIn('esc',self.keys);self.assertEqual(self.clicks,[])


if __name__=='__main__':unittest.main(verbosity=2)
