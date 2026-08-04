"""Core daemon tests. Run: python -m unittest discover -s host/daemon/tests

No device required — everything here is the host-side logic that Phase 0 delivers.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import color, config, effects, layout, palettes  # noqa: E402
from libremicro.frame import Frame  # noqa: E402
from libremicro.transport import Link  # noqa: E402


class TestColor(unittest.TestCase):
    def test_hex_roundtrip(self):
        for h in ("000000", "ffffff", "4a154b", "25d366", "ff9505"):
            self.assertEqual(color.to_hex(color.parse_hex(h)), h)

    def test_hex_accepts_leading_hash(self):
        self.assertEqual(color.parse_hex("#ff0000"), (1.0, 0.0, 0.0))

    def test_bad_hex_raises(self):
        for bad in ("", "fff", "gggggg", "12345", "1234567"):
            with self.assertRaises(ValueError):
                color.parse_hex(bad)

    def test_oklab_roundtrip(self):
        for h in ("123456", "abcdef", "ff8800", "00ff88"):
            rgb = color.parse_hex(h)
            back = color.oklab_to_rgb(color.rgb_to_oklab(rgb))
            for a, b in zip(rgb, back):
                self.assertAlmostEqual(a, b, places=4)

    def test_mix_endpoints_are_exact(self):
        a, b = color.parse_hex("ff0000"), color.parse_hex("0000ff")
        self.assertEqual(color.to_hex(color.mix(a, b, 0.0)), "ff0000")
        self.assertEqual(color.to_hex(color.mix(a, b, 1.0)), "0000ff")

    def test_mix_midpoint_stays_saturated(self):
        # The whole reason for OKLab: sRGB-interpolating red->blue dips through a dull
        # middle. Perceptual chroma at the midpoint should stay well clear of grey.
        mid = color.mix(color.parse_hex("ff0000"), color.parse_hex("0000ff"), 0.5)
        _, chroma, _ = color.rgb_to_oklch(mid)
        self.assertGreater(chroma, 0.10)

    def test_scale_lightness_monotonic(self):
        base = color.parse_hex("3388ff")
        levels = [color.rgb_to_oklch(color.scale_lightness(base, f))[0]
                  for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(levels, sorted(levels))


class TestPalette(unittest.TestCase):
    def test_single_stop_is_solid(self):
        p = color.Palette([(0.0, "ff0000")])
        self.assertEqual(color.to_hex(p.sample(0.0)), "ff0000")
        self.assertEqual(color.to_hex(p.sample(0.9)), "ff0000")

    def test_stops_are_sorted(self):
        p = color.Palette([(1.0, "0000ff"), (0.0, "ff0000")])
        self.assertEqual(color.to_hex(p.sample(0.0)), "ff0000")
        self.assertEqual(color.to_hex(p.sample(1.0)), "0000ff")

    def test_clamped_outside_range(self):
        p = color.Palette([(0.25, "ff0000"), (0.75, "0000ff")])
        self.assertEqual(color.to_hex(p.sample(0.0)), "ff0000")
        self.assertEqual(color.to_hex(p.sample(1.0)), "0000ff")

    def test_cyclic_wraps_without_seam(self):
        p = palettes.BUILTIN["rainbow"]
        near_end, wrapped = p.sample(0.999), p.sample(0.001)
        for a, b in zip(near_end, wrapped):
            self.assertLess(abs(a - b), 0.25)

    def test_ramp_lengths(self):
        for n in (1, 8, 13, 21):
            self.assertEqual(len(palettes.BUILTIN["sunset"].ramp(n)), n)

    def test_config_roundtrip(self):
        original = palettes.BUILTIN["aurora"]
        restored = color.Palette.from_config(original.to_config())
        self.assertEqual(restored.cyclic, original.cyclic)
        self.assertEqual([color.to_hex(c) for _, c in restored.stops],
                         [color.to_hex(c) for _, c in original.stops])

    def test_wled_import(self):
        p = palettes.import_wled({"palette": [0, 255, 0, 0, 255, 0, 0, 255]})
        self.assertEqual(color.to_hex(p.sample(0.0)), "ff0000")
        self.assertEqual(color.to_hex(p.sample(1.0)), "0000ff")

    def test_wled_import_rejects_ragged(self):
        with self.assertRaises(ValueError):
            palettes.import_wled({"palette": [0, 255, 0]})

    def test_builtin_corpus_is_valid(self):
        for name, p in palettes.BUILTIN.items():
            self.assertGreaterEqual(len(p.stops), 1, name)
            self.assertTrue(all(0.0 <= pos <= 1.0 for pos, _ in p.stops), name)


class TestLayout(unittest.TestCase):
    def test_row_counts(self):
        self.assertEqual(layout.KEY_ROWS, (2, 4, 4, 3))
        self.assertEqual(layout.KEY_N, 13)
        self.assertEqual(layout.UNDERGLOW_N, 8)

    def test_logical_rowcol_roundtrip(self):
        for i in range(layout.KEY_N):
            row, pos = layout.logical_to_rowcol(i)
            self.assertEqual(layout.rowcol_to_logical(row, pos), i)

    def test_row_boundaries(self):
        self.assertEqual(layout.logical_to_rowcol(0), (0, 0))
        self.assertEqual(layout.logical_to_rowcol(1), (0, 1))
        self.assertEqual(layout.logical_to_rowcol(2), (1, 0))
        self.assertEqual(layout.logical_to_rowcol(12), (3, 2))

    def test_out_of_range_raises(self):
        for bad in (-1, 13, 99):
            with self.assertRaises(IndexError):
                layout.logical_to_rowcol(bad)

    def test_narrow_rows_are_centred(self):
        # Row 0 has 2 keys against a widest row of 4, so it should sit inside the span.
        xs = [layout.key_xy(i)[0] for i in (0, 1)]
        self.assertTrue(all(0.0 < x < 1.0 for x in xs))
        self.assertAlmostEqual(sum(xs) / 2, 0.5, places=6)

    def test_underglow_ring_covers_grid_minus_centre(self):
        cells = set(layout.UNDERGLOW_RING)
        self.assertEqual(len(cells), 8)
        self.assertNotIn((1, 1), cells)

    def test_identity_mapping_when_unverified(self):
        lo = layout.Layout({})
        self.assertFalse(lo.verified)
        self.assertEqual(lo.logical_to_strip, list(range(13)))

    def test_config_mapping_is_applied(self):
        # Strip index 0 sits at row 3 position 2, i.e. logical 12.
        lo = layout.Layout({"key_positions": [[3, 2]], "verified": True})
        self.assertTrue(lo.verified)
        self.assertEqual(lo.strip_to_logical[0], 12)
        self.assertEqual(lo.logical_to_strip[12], 0)

    def test_bad_mapping_falls_back_to_identity(self):
        lo = layout.Layout({"key_positions": [[9, 9]]})
        self.assertEqual(lo.strip_to_logical[0], 0)


class TestFrame(unittest.TestCase):
    def test_blank_dimensions(self):
        f = Frame.blank()
        self.assertEqual((len(f.keys), len(f.under), len(f.status)), (13, 8, 3))

    def test_hex_roundtrip(self):
        f = Frame([color.parse_hex("ff0000")] * 13, [color.parse_hex("00ff00")] * 8, [1, 2, 3])
        back = Frame.from_hex(f.to_hex())
        self.assertEqual(back.to_hex(), f.to_hex())

    def test_from_hex_pads_short_input(self):
        f = Frame.from_hex({"keys": ["ffffff"], "underglow": [], "status": []})
        self.assertEqual(len(f.keys), 13)
        self.assertEqual(color.to_hex(f.keys[0]), "ffffff")
        self.assertEqual(color.to_hex(f.keys[1]), "000000")

    def test_composite_replace_respects_target(self):
        base = Frame([color.parse_hex("ff0000")] * 13, [color.parse_hex("ff0000")] * 8)
        top = Frame([color.parse_hex("0000ff")] * 13, [color.parse_hex("0000ff")] * 8)
        out = base.composite(top, "replace", "keys")
        self.assertEqual(color.to_hex(out.keys[0]), "0000ff")
        self.assertEqual(color.to_hex(out.under[0]), "ff0000")

    def test_composite_multiply_darkens(self):
        base = Frame([color.parse_hex("808080")] * 13)
        top = Frame([color.parse_hex("808080")] * 13)
        out = base.composite(top, "multiply", "keys")
        self.assertLess(out.keys[0][0], base.keys[0][0])

    def test_composite_amount_zero_is_noop(self):
        base = Frame([color.parse_hex("ff0000")] * 13)
        top = Frame([color.parse_hex("0000ff")] * 13)
        self.assertEqual(base.composite(top, "replace", "all", 0.0).to_hex(), base.to_hex())


class TestEffects(unittest.TestCase):
    def setUp(self):
        self.layout = layout.Layout({})

    def test_every_effect_renders_a_full_frame(self):
        for name in effects.EFFECT_NAMES:
            fx = effects.build({"name": name, "palette": "sunset"})
            self.assertIsNotNone(fx, name)
            for t in (0.0, 0.37, 1.5, 9.9):
                f = fx.render(t, self.layout)
                self.assertEqual(len(f.keys), 13, f"{name} @ {t}")
                self.assertEqual(len(f.under), 8, f"{name} @ {t}")
                for c in f.keys + f.under:
                    self.assertEqual(len(c), 3)
                    self.assertTrue(all(0.0 <= v <= 1.0 for v in c), f"{name} @ {t}: {c}")

    def test_unknown_effect_rejected(self):
        with self.assertRaises(ValueError):
            effects.build({"name": "definitely-not-an-effect"})

    def test_no_spec_is_no_effect(self):
        self.assertIsNone(effects.build(None))

    def test_gradient_varies_across_the_pad(self):
        fx = effects.build({"name": "gradient", "palette": "rainbow", "speed": 0,
                            "direction": "horizontal"})
        hexes = {color.to_hex(c) for c in fx.render(0.0, self.layout).keys}
        self.assertGreater(len(hexes), 3)

    def test_static_speed_is_stable_over_time(self):
        fx = effects.build({"name": "gradient", "palette": "ocean", "speed": 0})
        self.assertEqual(fx.render(0.0, self.layout).to_hex(),
                         fx.render(5.0, self.layout).to_hex())

    def test_animation_actually_moves(self):
        fx = effects.build({"name": "chase", "palette": "party", "speed": 1.0})
        self.assertNotEqual(fx.render(0.0, self.layout).to_hex(),
                            fx.render(0.5, self.layout).to_hex())

    def test_breathe_is_dimmer_at_trough(self):
        fx = effects.build({"name": "breathe", "palette": "mono", "speed": 1.0})
        at_trough = fx.render(0.0, self.layout).keys[0]
        at_peak = fx.render(0.5, self.layout).keys[0]
        self.assertLess(sum(at_trough), sum(at_peak))

    def test_off_is_black(self):
        f = effects.build({"name": "off"}).render(1.0, self.layout)
        self.assertEqual(set(f.to_hex()["keys"]), {"000000"})

    def test_ripple_trigger_lights_something(self):
        fx = effects.build({"name": "ripple", "palette": "ice", "speed": 1.0})
        fx.trigger_at(0.0, 0.5, 0.5)
        lit = [c for c in fx.render(0.3, self.layout).keys if sum(c) > 0.01]
        self.assertTrue(lit)


class TestFrameDiffing(unittest.TestCase):
    """Bandwidth matters: 115200 baud can't carry a full 21-pixel frame 30x a second."""

    def setUp(self):
        self.link = Link(port=None, layout=layout.Layout({}))

    def test_uniform_zone_collapses_to_one_command(self):
        f = Frame([color.parse_hex("ff0000")] * 13, [color.parse_hex("0000ff")] * 8)
        lines = list(self.link._frame_lines(f, None))
        self.assertIn("k all ff0000", lines)
        self.assertIn("u all 0000ff", lines)
        self.assertEqual(len([l for l in lines if l.startswith("k ")]), 1)

    def test_identical_frame_sends_nothing(self):
        f = Frame([color.parse_hex("ff0000")] * 13, [color.parse_hex("0000ff")] * 8)
        self.assertEqual(list(self.link._frame_lines(f, f.copy())), [])

    def test_single_changed_pixel_sends_one_command(self):
        prev = Frame([color.parse_hex("111111")] * 13, [color.parse_hex("222222")] * 8)
        nxt = prev.copy()
        nxt.keys[5] = color.parse_hex("ff0000")
        lines = list(self.link._frame_lines(nxt, prev))
        self.assertEqual(lines, ["k 5 ff0000"])

    def test_status_leds_are_diffed_too(self):
        prev = Frame(status=[0, 0, 0])
        nxt = Frame(status=[0, 128, 0])
        self.assertEqual([l for l in self.link._frame_lines(nxt, prev) if l.startswith("t ")],
                         ["t 1 128"])

    def test_mapping_is_honoured_when_writing(self):
        # Logical 12 lives at strip index 0, so a change there must address strip 0.
        link = Link(port=None, layout=layout.Layout({"key_positions": [[3, 2]]}))
        prev = Frame([color.parse_hex("000000")] * 13)
        nxt = prev.copy()
        nxt.keys[12] = color.parse_hex("ff0000")
        self.assertEqual(list(link._frame_lines(nxt, prev)), ["k 0 ff0000"])


class TestConfig(unittest.TestCase):
    def test_example_config_is_valid(self):
        cfg = config.Config.load(config.EXAMPLE_PATH)
        self.assertEqual(cfg.doc["version"], 2)
        self.assertIn("default", cfg.profile_names)

    def test_shipped_schema_matches_shipped_example(self):
        cfg = config.Config(json.loads(config.EXAMPLE_PATH.read_text()))
        hard = [e for e in config.validate(cfg.doc) if not e.endswith("skipped validation")]
        self.assertEqual(hard, [])

    def test_v1_migration(self):
        v1 = {
            "port": "auto", "brightness": 180,
            "keys": [
                {"index": 0, "label": "Slack", "launch": "Slack", "color": "4a154b"},
                {"index": 4, "label": "Chrome", "command": "open -a Chrome"},
                {"index": 6, "label": "Media", "mode": "media"},
            ],
            "modes": {"media": {"activate_key": 6, "flash": "00ff88",
                                "encoder": {"cw": "vol_up", "press": "play_pause"}}},
            "default_encoder": {"cw": "vol_up", "ccw": "shell:echo hi"},
        }
        cfg = config.Config(v1)
        self.assertEqual(cfg.doc["version"], 2)
        profile = cfg.profile("default")
        self.assertEqual(profile["keys"][0]["on"]["press"], {"launch": "Slack"})
        self.assertEqual(profile["keys"][1]["on"]["press"], {"shell": "open -a Chrome"})
        self.assertEqual(profile["keys"][2]["on"]["press"], {"mode": "media"})
        self.assertEqual(profile["encoder"]["ccw"], {"shell": "echo hi"})
        self.assertEqual(cfg.brightness, 180)
        self.assertEqual(config.validate(cfg.doc), [])

    def test_migrated_v1_passes_schema(self):
        cfg = config.Config({"keys": [{"index": 0, "launch": "Slack"}]})
        hard = [e for e in config.validate(cfg.doc) if not e.endswith("skipped validation")]
        self.assertEqual(hard, [])

    def test_unknown_profile_raises(self):
        cfg = config.Config({"version": 2, "profiles": {"default": {}}})
        with self.assertRaises(config.ConfigError):
            cfg.profile("nope")

    def test_custom_palette_is_available(self):
        cfg = config.Config({
            "version": 2,
            "palettes": {"mine": {"stops": [{"pos": 0, "color": "ff0000"}]}},
            "profiles": {"default": {}},
        })
        self.assertIn("mine", cfg.palettes)
        self.assertIn("rainbow", cfg.palettes)  # built-ins still present

    def test_export_inlines_referenced_builtin_palette(self):
        cfg = config.Config({
            "version": 2,
            "profiles": {"default": {"lighting": {
                "effect": {"name": "gradient", "palette": "aurora"}}}},
        })
        bundle = cfg.export_bundle()
        self.assertIn("aurora", bundle["palettes"])
        self.assertTrue(bundle["palettes"]["aurora"]["stops"])

    def test_export_import_roundtrip(self):
        cfg = config.Config.load(config.EXAMPLE_PATH)
        restored = config.Config.import_bundle(cfg.export_bundle())
        self.assertEqual(restored.profile_names, cfg.profile_names)

    def test_edits_never_land_on_the_shipped_example(self):
        cfg = config.Config.load(config.EXAMPLE_PATH)
        self.assertEqual(cfg.path, config.EXAMPLE_PATH)
        self.assertEqual(cfg.save_path, config.USER_CONFIG_PATH)

    def test_save_path_keeps_an_explicit_path(self):
        cfg = config.Config({"version": 2, "profiles": {"default": {}}})
        cfg.path = Path("/tmp/somewhere/config.json")
        self.assertEqual(cfg.save_path, cfg.path)

    def test_save_is_atomic_and_reloadable(self):
        import tempfile
        cfg = config.Config.load(config.EXAMPLE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            cfg.save(target)
            self.assertTrue(target.is_file())
            self.assertFalse(list(Path(tmp).glob("*.tmp")))
            self.assertEqual(config.Config.load(target).profile_names, cfg.profile_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
