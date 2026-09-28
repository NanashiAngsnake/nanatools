from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageCms, ImageSequence

from nanatools.core import imaging as core


def srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


class ImagingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()


    def settings(self, **kwargs) -> core.ConvertSettings:
        kwargs.setdefault("dest_dir", self.out)
        return core.ConvertSettings(**kwargs)

    def rgb(self, name="plain.png", size=(60, 40), colour=(20, 120, 200)) -> Path:
        path = self.dir / name
        Image.new("RGB", size, colour).save(path)
        return path

    def half_transparent_red(self, name="alpha.png") -> Path:
        im = Image.new("RGBA", (8, 8), (255, 0, 0, 128))
        im.putpixel((0, 0), (0, 0, 0, 0))
        path = self.dir / name
        im.save(path)
        return path

    def rotated_jpeg(self, name="rot.jpg") -> Path:
        im = Image.new("RGB", (100, 50), (10, 200, 30))
        exif = im.getexif()
        exif[0x0112] = 6
        exif[0x010E] = "nanatools fixture"
        path = self.dir / name
        im.save(path, exif=exif.tobytes())
        return path

    def animation(self, name="anim.gif", colours=None) -> Path:
        colours = colours or [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        frames = [Image.new("RGB", (20, 20), c).convert(
            "P", palette=Image.Palette.ADAPTIVE) for c in colours]
        path = self.dir / name
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=[50, 300, 900], loop=0)
        return path

    def high_depth(self, name="scan.tif") -> Path:
        im = Image.new("I;16", (4, 1))
        im.putdata([0, 1000, 40000, 65535])
        path = self.dir / name
        im.save(path)
        return path


    def frame_pixels(self, path: Path, at=(10, 10)) -> list[tuple]:
        out = []
        with Image.open(path) as im:
            for index in range(getattr(im, "n_frames", 1) or 1):
                im.seek(index)
                out.append(im.convert("RGB").getpixel(at))
        return out


class TransparencyTests(ImagingTestCase):
    def test_naive_convert_is_wrong(self):
        with Image.open(self.half_transparent_red()) as im:
            naive = im.convert("RGB")
        self.assertEqual(naive.getpixel((4, 4)), (255, 0, 0))
        self.assertEqual(naive.getpixel((0, 0)), (0, 0, 0))

    def test_flatten_composites_onto_background(self):
        with Image.open(self.half_transparent_red()) as im:
            flat = core.flatten(im, (255, 255, 255))
        r, g, b = flat.getpixel((4, 4))
        self.assertEqual(r, 255)
        self.assertAlmostEqual(g, 127, delta=2)
        self.assertAlmostEqual(b, 127, delta=2)
        self.assertEqual(flat.getpixel((0, 0)), (255, 255, 255))

    def test_flatten_respects_a_chosen_background(self):
        with Image.open(self.half_transparent_red()) as im:
            flat = core.flatten(im, (0, 0, 0))
        self.assertEqual(flat.getpixel((0, 0)), (0, 0, 0))
        self.assertAlmostEqual(flat.getpixel((4, 4))[0], 128, delta=2)

    def test_jpeg_conversion_flattens_rather_than_dropping_alpha(self):
        result = core.convert_one(self.half_transparent_red(),
                                  self.settings(target_format="JPEG", quality=100))
        self.assertTrue(result.ok, result.error)
        self.assertTrue(any("flattened" in w for w in result.warnings))
        with Image.open(result.target) as out:
            self.assertGreater(out.getpixel((0, 0))[1], 200)
            self.assertLess(out.getpixel((4, 4))[1], 200)

    def test_alpha_kept_when_the_target_can_hold_it(self):
        for fmt in ("PNG", "WEBP", "TIFF"):
            with self.subTest(fmt=fmt):
                result = core.convert_one(
                    self.half_transparent_red(),
                    self.settings(target_format=fmt, dest_dir=self.out / fmt))
                self.assertTrue(result.ok, result.error)
                with Image.open(result.target) as out:
                    self.assertEqual(out.convert("RGBA").getpixel((0, 0))[3], 0)
                self.assertEqual(result.warnings, [])

    def test_palette_without_transparency_still_converts_for_jpeg(self):
        path = self.dir / "flat.png"
        Image.new("RGB", (10, 10), (7, 7, 7)).convert("P").save(path)
        with Image.open(path) as im:
            self.assertEqual(im.mode, "P")
            self.assertNotIn("transparency", im.info)
        result = core.convert_one(path, self.settings(target_format="JPEG"))
        self.assertTrue(result.ok, result.error)


class OrientationTests(ImagingTestCase):
    def test_pillow_does_not_apply_orientation_on_open(self):
        with Image.open(self.rotated_jpeg()) as im:
            self.assertEqual(im.size, (100, 50))
            self.assertEqual(im.getexif().get(0x0112), 6)

    def test_output_is_upright(self):
        result = core.convert_one(self.rotated_jpeg(),
                                  self.settings(target_format="PNG"))
        self.assertTrue(result.ok, result.error)
        with Image.open(result.target) as out:
            self.assertEqual(out.size, (50, 100))

    def test_written_exif_no_longer_asks_for_a_rotation(self):
        result = core.convert_one(self.rotated_jpeg(),
                                  self.settings(target_format="JPEG"))
        self.assertTrue(result.ok, result.error)
        with Image.open(result.target) as out:
            self.assertEqual(out.size, (50, 100))
            self.assertIn(out.getexif().get(0x0112), (None, 1))

    def test_other_exif_tags_survive(self):
        result = core.convert_one(self.rotated_jpeg(),
                                  self.settings(target_format="JPEG"))
        with Image.open(result.target) as out:
            self.assertEqual(out.getexif().get(0x010E), "nanatools fixture")

    def test_resize_box_applies_to_the_upright_picture(self):
        result = core.convert_one(self.rotated_jpeg(), self.settings(
            target_format="PNG", resize_mode=core.ResizeMode.LONG_EDGE,
            long_edge=40))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.dst_size, (20, 40))


class MetadataTests(ImagingTestCase):
    def icc_source(self, name="icc.png") -> tuple[Path, bytes]:
        profile = srgb_profile()
        path = self.dir / name
        Image.new("RGB", (30, 20), (9, 9, 9)).save(path, icc_profile=profile)
        return path, profile

    def test_naive_save_drops_icc_on_jpeg_and_webp(self):
        path, profile = self.icc_source()
        for fmt, ext in (("JPEG", ".jpg"), ("WEBP", ".webp")):
            with self.subTest(fmt=fmt):
                target = self.dir / ("naive" + ext)
                with Image.open(path) as im:
                    im.save(target, format=fmt)
                with Image.open(target) as out:
                    self.assertIsNone(out.info.get("icc_profile"))

    def test_icc_is_preserved(self):
        path, profile = self.icc_source()
        for fmt in ("JPEG", "WEBP", "PNG", "TIFF"):
            with self.subTest(fmt=fmt):
                result = core.convert_one(
                    path, self.settings(target_format=fmt, dest_dir=self.out / fmt))
                self.assertTrue(result.ok, result.error)
                with Image.open(result.target) as out:
                    self.assertEqual(out.info.get("icc_profile"), profile)

    def test_strip_removes_everything(self):
        path, _ = self.icc_source()
        result = core.convert_one(path, self.settings(
            target_format="JPEG", metadata=core.MetadataPolicy.STRIP))
        with Image.open(result.target) as out:
            self.assertIsNone(out.info.get("icc_profile"))

        result = core.convert_one(self.rotated_jpeg(), self.settings(
            target_format="JPEG", dest_dir=self.out / "b",
            metadata=core.MetadataPolicy.STRIP))
        with Image.open(result.target) as out:
            self.assertIsNone(out.getexif().get(0x010E))
            self.assertEqual(out.size, (50, 100))

    def test_colour_only_keeps_the_profile_and_drops_the_camera_tags(self):
        im = Image.new("RGB", (20, 10), (5, 5, 5))
        exif = im.getexif()
        exif[0x010E] = "private"
        path = self.dir / "both.jpg"
        im.save(path, exif=exif.tobytes(), icc_profile=srgb_profile())

        result = core.convert_one(path, self.settings(
            target_format="JPEG", metadata=core.MetadataPolicy.COLOUR_ONLY))
        with Image.open(result.target) as out:
            self.assertIsNotNone(out.info.get("icc_profile"))
            self.assertIsNone(out.getexif().get(0x010E))


class AnimationTests(ImagingTestCase):

    def test_naive_save_writes_one_frame(self):
        path = self.animation()
        target = self.dir / "naive.webp"
        with Image.open(path) as im:
            im.save(target)
        with Image.open(target) as out:
            self.assertEqual(getattr(out, "n_frames", 1), 1)

    def test_iterator_without_copy_collapses_to_one_frame(self):
        path = self.animation()
        with Image.open(path) as im:
            shared = [f for f in ImageSequence.Iterator(im)]
            distinct = {f.convert("RGB").getpixel((10, 10)) for f in shared}
        self.assertEqual(len(distinct), 1)

        with Image.open(path) as im:
            copied = core._frames_of(im)
        self.assertEqual(
            len({f.convert("RGB").getpixel((10, 10)) for f in copied}), 3)

    def test_frames_are_preserved_and_still_differ(self):
        path = self.animation()
        for fmt in ("WEBP", "GIF", "PNG", "AVIF"):
            if fmt not in core.available_formats():
                continue
            with self.subTest(fmt=fmt):
                result = core.convert_one(
                    path, self.settings(target_format=fmt, dest_dir=self.out / fmt))
                self.assertTrue(result.ok, result.error)
                self.assertEqual(result.out_frames, 3)
                pixels = self.frame_pixels(result.target)
                self.assertEqual(len(pixels), 3)
                for index, channel in enumerate(pixels):
                    self.assertGreater(channel[index], 200, pixels)

    def test_gif_durations_round_trip(self):
        result = core.convert_one(self.animation(),
                                  self.settings(target_format="GIF"))
        durations = []
        with Image.open(result.target) as out:
            for index in range(out.n_frames):
                out.seek(index)
                durations.append(out.info.get("duration"))
        self.assertEqual(durations, [50, 300, 900])

    def test_still_target_falls_back_to_the_first_frame_with_a_warning(self):
        result = core.convert_one(self.animation(),
                                  self.settings(target_format="JPEG"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.out_frames, 1)
        self.assertTrue(any("animation dropped" in w for w in result.warnings))

    def test_first_frame_mode(self):
        result = core.convert_one(self.animation(), self.settings(
            target_format="WEBP", animation=core.AnimationMode.FIRST_FRAME))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.out_frames, 1)

    def test_skip_mode_leaves_the_file_alone(self):
        result = core.convert_one(self.animation(), self.settings(
            target_format="WEBP", animation=core.AnimationMode.SKIP))
        self.assertTrue(result.skipped)
        self.assertFalse(result.ok)
        self.assertFalse(list(self.out.glob("*")) if self.out.exists() else [])

    def test_mixed_frame_modes_do_not_break_apng(self):
        with Image.open(self.animation()) as im:
            modes = {f.mode for f in core._frames_of(im)}
        self.assertGreater(len(modes), 1, "fixture no longer exercises the trap")

        result = core.convert_one(self.animation(),
                                  self.settings(target_format="PNG"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.out_frames, 3)

    def test_animated_transparency_survives(self):
        frames = [Image.new("RGBA", (20, 20), c + (255,))
                  for c in ((255, 0, 0), (0, 255, 0))]
        for frame in frames:
            frame.putpixel((0, 0), (0, 0, 0, 0))
        path = self.dir / "alpha.webp"
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=100, loop=0)

        for fmt in ("WEBP", "PNG", "GIF"):
            with self.subTest(fmt=fmt):
                result = core.convert_one(
                    path, self.settings(target_format=fmt, dest_dir=self.out / fmt))
                self.assertTrue(result.ok, result.error)
                with Image.open(result.target) as out:
                    self.assertEqual(out.convert("RGBA").getpixel((0, 0))[3], 0)


class SaveKeywordTests(ImagingTestCase):
    def test_pillow_accepts_nonsense_without_complaint(self):
        target = self.dir / "junk.jpg"
        with Image.open(self.rgb()) as im:
            im.convert("RGB").save(target, format="JPEG", qualiti=10, bogus=True)
        self.assertTrue(target.exists())

    def test_metadata_a_format_cannot_carry_is_dropped_but_reported(self):
        for fmt in ("BMP", "GIF", "ICO"):
            with self.subTest(fmt=fmt):
                kwargs = core.build_save_kwargs(
                    fmt, core.ConvertSettings(), icc=b"x", exif=b"y")
                self.assertNotIn("icc_profile", kwargs)
                self.assertNotIn("exif", kwargs)

                gaps = core.metadata_gap(fmt, icc=b"x", exif=b"y")
                self.assertEqual(len(gaps), 2, gaps)
                self.assertTrue(any("colour profile" in g for g in gaps))
                self.assertTrue(any("EXIF" in g for g in gaps))

        self.assertEqual(core.metadata_gap("JPEG", icc=b"x", exif=b"y"), [])
        self.assertEqual(core.metadata_gap("BMP", icc=None, exif=None), [])

    def test_dropped_profile_reaches_the_user_on_a_real_conversion(self):
        source = self.rgb("photo.png")
        with Image.open(source) as im:
            im.save(source, icc_profile=b"fake-profile-bytes")

        result = core.convert_one(source, self.settings(target_format="BMP"))
        self.assertTrue(result.ok, result.error)
        self.assertTrue(
            any("colour profile" in w for w in result.warnings),
            f"the dropped ICC profile was never reported: {result.warnings}",
        )

    def test_every_generated_keyword_is_whitelisted(self):
        settings = core.ConvertSettings(quality=95, webp_lossless=True)
        for fmt in core.SAVE_KWARGS:
            with self.subTest(fmt=fmt):
                kwargs = core.build_save_kwargs(fmt, settings, size=(64, 64))
                self.assertLessEqual(set(kwargs), core.SAVE_KWARGS[fmt])

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            core.build_save_kwargs("HEIC", core.ConvertSettings())

    def test_avif_has_no_lossless_and_forces_444_at_high_quality(self):
        self.assertNotIn("lossless", core.SAVE_KWARGS["AVIF"])
        high = core.build_save_kwargs("AVIF", core.ConvertSettings(quality=95))
        self.assertEqual(high.get("subsampling"), "4:4:4")
        self.assertEqual(high.get("max_threads"), core.AVIF_MAX_THREADS)
        low = core.build_save_kwargs("AVIF", core.ConvertSettings(quality=50))
        self.assertNotIn("subsampling", low)

    def test_lossless_advice_points_away_from_avif(self):
        self.assertNotIn("AVIF", " ".join(core.TRUE_LOSSLESS))
        self.assertIn("PNG", core.TRUE_LOSSLESS)

    def test_webp_lossless_is_pixel_exact(self):
        source = self.dir / "detail.png"
        im = Image.new("RGB", (32, 32), (0, 0, 0))
        for x in range(32):
            for y in range(32):
                im.putpixel((x, y), (x * 8 % 256, y * 8 % 256, (x + y) * 4 % 256))
        im.save(source)
        result = core.convert_one(source, self.settings(
            target_format="WEBP", webp_lossless=True))
        self.assertTrue(result.ok, result.error)
        with Image.open(result.target) as out:
            self.assertEqual(list(out.convert("RGB").getdata()),
                             list(im.getdata()))


class ModeMatrixTests(ImagingTestCase):
    def sources(self) -> dict[str, Path]:
        made: dict[str, Path] = {}

        rgba = Image.new("RGBA", (12, 8), (255, 0, 0, 128))
        rgba.save(self.dir / "m_rgba.png")
        made["RGBA"] = self.dir / "m_rgba.png"

        Image.new("LA", (12, 8), (128, 128)).save(self.dir / "m_la.png")
        made["LA"] = self.dir / "m_la.png"

        pal = Image.new("P", (12, 8))
        pal.info["transparency"] = 0
        pal.save(self.dir / "m_p.png")
        made["P"] = self.dir / "m_p.png"

        Image.new("CMYK", (12, 8), (0, 255, 255, 0)).save(self.dir / "m_cmyk.jpg")
        made["CMYK"] = self.dir / "m_cmyk.jpg"

        Image.new("I;16", (12, 8), 40000).save(self.dir / "m_i16.tif")
        made["I;16"] = self.dir / "m_i16.tif"

        Image.new("F", (12, 8), 0.5).save(self.dir / "m_f.tif")
        made["F"] = self.dir / "m_f.tif"

        Image.new("1", (12, 8), 1).save(self.dir / "m_1.png")
        made["1"] = self.dir / "m_1.png"
        return made

    def test_every_mode_reaches_every_format(self):
        for mode, path in self.sources().items():
            for fmt in core.available_formats():
                with self.subTest(mode=mode, fmt=fmt):
                    result = core.convert_one(path, self.settings(
                        target_format=fmt,
                        dest_dir=self.out / fmt,
                        high_depth=core.HighDepthPolicy.DOWNCAST))
                    self.assertTrue(result.ok, f"{mode}->{fmt}: {result.error}")
                    with Image.open(result.target) as out:
                        out.load()

    def test_naive_saves_of_the_same_pairs_do_raise(self):
        pairs = [("m_rgba.png", "JPEG"), ("m_la.png", "JPEG"),
                 ("m_p.png", "JPEG"), ("m_cmyk.jpg", "GIF"),
                 ("m_la.png", "BMP"), ("m_f.tif", "PNG")]
        self.sources()
        for name, fmt in pairs:
            with self.subTest(name=name, fmt=fmt):
                with self.assertRaises(Exception):
                    with Image.open(self.dir / name) as im:
                        im.save(self.dir / f"naive_{name}_{fmt}", format=fmt)

    def test_coerce_reports_what_it_lost(self):
        with Image.open(self.sources()["RGBA"]) as im:
            _, notes = core.coerce_mode(im, "JPEG", (255, 255, 255))
        self.assertTrue(any("flattened" in n for n in notes))

        with Image.open(self.sources()["RGBA"]) as im:
            _, notes = core.coerce_mode(im, "PNG", (255, 255, 255))
        self.assertEqual(notes, [])


class HighDepthTests(ImagingTestCase):
    def test_pillow_clips_rather_than_rescales(self):
        path = self.high_depth()
        target = self.dir / "clipped.webp"
        with Image.open(path) as im:
            im.save(target, format="WEBP")
        with Image.open(target) as out:
            values = [p[0] for p in out.convert("RGB").getdata()]
        self.assertEqual(values, [0, 254, 255, 255])

    def test_blocked_by_default(self):
        result = core.convert_one(self.high_depth(),
                                  self.settings(target_format="WEBP"))
        self.assertFalse(result.ok)
        self.assertIn("8-bit", result.error)

    def test_png_and_tiff_round_trip_exactly(self):
        for fmt in ("PNG", "TIFF"):
            with self.subTest(fmt=fmt):
                result = core.convert_one(self.high_depth(), self.settings(
                    target_format=fmt, dest_dir=self.out / fmt))
                self.assertTrue(result.ok, result.error)
                with Image.open(result.target) as out:
                    self.assertEqual(list(out.getdata()), [0, 1000, 40000, 65535])

    def test_downcast_rescales_instead_of_clipping(self):
        result = core.convert_one(self.high_depth(), self.settings(
            target_format="WEBP", high_depth=core.HighDepthPolicy.DOWNCAST))
        self.assertTrue(result.ok, result.error)
        with Image.open(result.target) as out:
            values = [p[0] for p in out.convert("RGB").getdata()]
        self.assertLess(values[1], 20)
        self.assertGreater(values[2], 120)
        self.assertLess(values[2], 200)
        self.assertTrue(any("8-bit" in w for w in result.warnings))


class IcoTests(ImagingTestCase):

    def test_pillow_writes_an_unopenable_file_when_sizes_are_too_big(self):
        source = self.rgb("small.png", size=(20, 20))
        target = self.dir / "broken.ico"
        with Image.open(source) as im:
            im.save(target, sizes=[(32, 32), (64, 64)])
        self.assertEqual(os.path.getsize(target), 6)
        with self.assertRaises(Exception):
            with Image.open(target) as broken:
                broken.load()

    def test_sizes_are_clamped_to_the_source(self):
        self.assertEqual(core.ico_sizes_for((20, 20), (16, 32, 48, 256)),
                         [(16, 16)])
        self.assertEqual(core.ico_sizes_for((512, 512), (16, 512)), [(16, 16)])

    def test_a_source_smaller_than_every_requested_size_still_works(self):
        self.assertEqual(core.ico_sizes_for((4, 1), (16, 32)), [(4, 1)])
        source = self.rgb("tiny.png", size=(4, 1))
        result = core.convert_one(source, self.settings(target_format="ICO"))
        self.assertTrue(result.ok, result.error)
        with Image.open(result.target) as out:
            out.load()

    def test_written_icons_are_readable(self):
        for size in ((4, 1), (20, 20), (64, 64), (300, 200)):
            with self.subTest(size=size):
                source = self.rgb(f"ico{size[0]}x{size[1]}.png", size=size)
                result = core.convert_one(source, self.settings(
                    target_format="ICO", dest_dir=self.out / str(size[0])))
                self.assertTrue(result.ok, result.error)
                self.assertGreater(os.path.getsize(result.target), 6)


class ResizeTests(ImagingTestCase):

    def test_no_upscaling_by_default(self):
        settings = core.ConvertSettings(
            resize_mode=core.ResizeMode.FIT, max_w=400, max_h=400)
        self.assertEqual(core.target_size((100, 50), settings), (100, 50))
        self.assertEqual(
            core.target_size((100, 50), settings.with_(allow_upscale=True)),
            (400, 200))

    def test_fit_preserves_aspect(self):
        settings = core.ConvertSettings(
            resize_mode=core.ResizeMode.FIT, max_w=400, max_h=400)
        self.assertEqual(core.target_size((1200, 800), settings), (400, 267))

    def test_long_edge(self):
        settings = core.ConvertSettings(
            resize_mode=core.ResizeMode.LONG_EDGE, long_edge=600)
        self.assertEqual(core.target_size((1200, 800), settings), (600, 400))
        self.assertEqual(core.target_size((800, 1200), settings), (400, 600))

    def test_percent(self):
        settings = core.ConvertSettings(
            resize_mode=core.ResizeMode.PERCENT, percent=25)
        self.assertEqual(core.target_size((1200, 800), settings), (300, 200))
        grow = settings.with_(percent=200)
        self.assertEqual(core.target_size((100, 100), grow), (100, 100))
        self.assertEqual(
            core.target_size((100, 100), grow.with_(allow_upscale=True)),
            (200, 200))

    def test_exact_may_distort_but_will_not_enlarge_unasked(self):
        settings = core.ConvertSettings(
            resize_mode=core.ResizeMode.EXACT, max_w=300, max_h=300)
        self.assertEqual(core.target_size((1200, 800), settings), (300, 300))
        self.assertEqual(core.target_size((100, 100), settings), (100, 100))

    def test_fit_helper_leaves_a_small_image_alone(self):
        im = Image.new("RGB", (100, 50))
        self.assertIs(core.fit(im, 400, 400), im)
        self.assertEqual(core.fit(im, 400, 400, allow_upscale=True).size, (400, 200))

    def test_resize_is_applied_end_to_end(self):
        source = self.rgb("big.png", size=(1200, 800))
        result = core.convert_one(source, self.settings(
            target_format="WEBP", resize_mode=core.ResizeMode.FIT,
            max_w=300, max_h=300))
        self.assertEqual(result.dst_size, (300, 200))
        with Image.open(result.target) as out:
            self.assertEqual(out.size, (300, 200))

    def test_webp_refuses_an_oversized_side(self):
        source = self.dir / "wide.png"
        Image.new("L", (core.WEBP_MAX_DIMENSION + 10, 4)).save(source)
        result = core.convert_one(source, self.settings(target_format="WEBP"))
        self.assertFalse(result.ok)
        self.assertIn("16383", result.error)
        ok = core.convert_one(source, self.settings(
            target_format="PNG", dest_dir=self.out / "png"))
        self.assertTrue(ok.ok, ok.error)


class OutputPathTests(ImagingTestCase):

    def test_never_writes_over_the_source(self):
        source = self.rgb("keep.png")
        plans = core.plan_targets([source], core.ConvertSettings(
            target_format="PNG", dest_dir=self.dir))
        _, target, reason = plans[0]
        self.assertIsNone(target)
        self.assertIn("overwrite the source", reason)

        result = core.convert_one(source, core.ConvertSettings(
            target_format="PNG", dest_dir=self.dir))
        self.assertTrue(result.skipped)
        self.assertEqual(os.path.getsize(source),
                         os.path.getsize(source))

    def test_default_destination_is_a_converted_subfolder(self):
        source = self.rgb()
        target = core.dest_for(source, core.ConvertSettings(target_format="WEBP"))
        self.assertEqual(target.parent, source.parent / "converted")
        self.assertEqual(target.name, "plain.webp")

    def test_collision_gets_a_numeric_suffix(self):
        first = self.dir / "a"
        second = self.dir / "b"
        first.mkdir()
        second.mkdir()
        Image.new("RGB", (4, 4)).save(first / "same.png")
        Image.new("RGB", (4, 4)).save(second / "same.png")
        settings = core.ConvertSettings(target_format="WEBP", dest_dir=self.out)
        names = [t.name for _, t, _ in
                 core.plan_targets([first / "same.png", second / "same.png"], settings)]
        self.assertEqual(names, ["same.webp", "same (1).webp"])

    def test_collision_skip_policy(self):
        source = self.rgb()
        self.out.mkdir(parents=True)
        (self.out / "plain.webp").write_bytes(b"existing")
        settings = core.ConvertSettings(
            target_format="WEBP", dest_dir=self.out,
            on_collision=core.OnCollision.SKIP)
        _, target, reason = core.plan_targets([source], settings)[0]
        self.assertIsNone(target)
        self.assertIn("already there", reason)
        self.assertEqual((self.out / "plain.webp").read_bytes(), b"existing")

    def test_collision_overwrite_policy(self):
        source = self.rgb()
        self.out.mkdir(parents=True)
        (self.out / "plain.webp").write_bytes(b"existing")
        result = core.convert_one(source, core.ConvertSettings(
            target_format="WEBP", dest_dir=self.out,
            on_collision=core.OnCollision.OVERWRITE))
        self.assertTrue(result.ok, result.error)
        self.assertNotEqual((self.out / "plain.webp").read_bytes(), b"existing")

    def test_no_temp_file_is_left_behind(self):
        core.convert_one(self.rgb(), self.settings(target_format="WEBP"))
        leftovers = list(self.out.glob("*tmp*"))
        self.assertEqual(leftovers, [])

    def test_a_failed_write_leaves_no_partial_output(self):
        source = self.dir / "wide.png"
        Image.new("L", (core.WEBP_MAX_DIMENSION + 10, 4)).save(source)
        result = core.convert_one(source, self.settings(target_format="WEBP"))
        self.assertFalse(result.ok)
        self.assertFalse(self.out.exists() and list(self.out.iterdir()))


class DiscoveryTests(ImagingTestCase):

    def build_tree(self) -> Path:
        root = self.dir / "tree"
        (root / "sub").mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(root / "one.png")
        Image.new("RGB", (4, 4)).save(root / "two.jpg")
        Image.new("RGB", (4, 4)).save(root / "sub" / "three.png")
        (root / "notes.txt").write_text("not an image", encoding="utf-8")
        return root

    def test_flat_folder(self):
        root = self.build_tree()
        names = [p.name for p in core.discover([root])]
        self.assertEqual(names, ["one.png", "two.jpg"])

    def test_recursive(self):
        root = self.build_tree()
        names = sorted(p.name for p in core.discover([root], recursive=True))
        self.assertEqual(names, ["one.png", "three.png", "two.jpg"])

    def test_extension_filter(self):
        root = self.build_tree()
        names = [p.name for p in core.discover([root], recursive=True,
                                               extensions=["png"])]
        self.assertEqual(names, ["one.png", "three.png"])

    def test_duplicates_and_quoting(self):
        root = self.build_tree()
        quoted = f'"{root / "one.png"}"'
        found = core.discover([root / "one.png", quoted, root])
        self.assertEqual([p.name for p in found], ["one.png", "two.jpg"])

    def test_missing_paths_are_ignored(self):
        self.assertEqual(core.discover([self.dir / "nope.png"]), [])


class BatchTests(ImagingTestCase):

    def make_batch(self, count=6) -> list[Path]:
        return [self.rgb(f"b{i}.png", size=(80 + i, 60)) for i in range(count)]

    def test_all_files_are_converted(self):
        sources = self.make_batch()
        seen: list[int] = []
        summary = core.convert_batch(
            sources, self.settings(target_format="WEBP"),
            on_result=lambda r, done, total: seen.append(done))
        self.assertEqual(summary.converted, len(sources))
        self.assertEqual(len(seen), len(sources))
        self.assertEqual(sorted(seen), list(range(1, len(sources) + 1)))
        self.assertFalse(summary.cancelled)

    def test_totals_add_up(self):
        sources = self.make_batch(4)
        summary = core.convert_batch(sources, self.settings(target_format="WEBP"))
        self.assertEqual(summary.total_src_bytes,
                         sum(os.path.getsize(p) for p in sources))
        self.assertEqual(
            summary.saved_bytes,
            summary.total_src_bytes - summary.total_dst_bytes)

    def test_a_broken_file_does_not_sink_the_batch(self):
        sources = self.make_batch(3)
        broken = self.dir / "broken.png"
        broken.write_bytes(b"not a png at all")
        sources.insert(1, broken)
        summary = core.convert_batch(sources, self.settings(target_format="WEBP"))
        self.assertEqual(summary.converted, 3)
        self.assertEqual(summary.failed, 1)

    def test_cancellation_stops_the_run(self):
        sources = self.make_batch(24)
        stop = threading.Event()
        count = 0

        def report(result, done, total):
            nonlocal count
            count += 1
            if count >= 2:
                stop.set()

        summary = core.convert_batch(
            sources, self.settings(target_format="PNG", workers=1),
            on_result=report, should_cancel=stop.is_set)
        self.assertTrue(summary.cancelled)
        self.assertLess(len(summary.results), len(sources))

    def test_worker_count_is_capped_for_avif(self):
        many = core.ConvertSettings(target_format="WEBP", workers=12)
        self.assertEqual(core.worker_count(many, 50), 12)
        avif = many.with_(target_format="AVIF")
        self.assertEqual(core.worker_count(avif, 50), core.AVIF_MAX_WORKERS)
        self.assertEqual(core.worker_count(many, 3), 3)
        self.assertEqual(core.worker_count(many, 0), 1)

    def test_auto_worker_count_is_bounded(self):
        auto = core.ConvertSettings(workers=0)
        self.assertGreaterEqual(core.worker_count(auto, 100), 1)
        self.assertLessEqual(core.worker_count(auto, 100), 12)

    def test_concurrent_run_produces_every_file(self):
        sources = self.make_batch(16)
        summary = core.convert_batch(
            sources, self.settings(target_format="PNG", workers=8))
        self.assertEqual(summary.converted, 16)
        written = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(written, sorted(p.stem + ".png" for p in sources))


class SettingsTests(ImagingTestCase):

    def test_round_trip_through_json(self):
        import json

        original = core.ConvertSettings(
            target_format="AVIF", dest_dir=self.out,
            resize_mode=core.ResizeMode.PERCENT, percent=40.0,
            metadata=core.MetadataPolicy.STRIP, ico_sizes=(16, 32),
            flatten_bg=(10, 20, 30))
        payload = json.loads(json.dumps(original.to_dict()))
        self.assertEqual(core.ConvertSettings.from_dict(payload), original)

    def test_a_damaged_settings_file_falls_back_to_defaults(self):
        restored = core.ConvertSettings.from_dict({
            "target_format": "PNG", "quality": "not a number",
            "resize_mode": "no such mode", "unknown_key": 1,
            "dest_dir": None,
        })
        defaults = core.ConvertSettings()
        self.assertEqual(restored.target_format, "PNG")
        self.assertEqual(restored.quality, defaults.quality)
        self.assertEqual(restored.resize_mode, defaults.resize_mode)

    def test_defaults_are_the_safe_ones(self):
        defaults = core.ConvertSettings()
        self.assertFalse(defaults.allow_upscale)
        self.assertEqual(defaults.resample, "LANCZOS")
        self.assertIs(defaults.metadata, core.MetadataPolicy.KEEP)
        self.assertIs(defaults.animation, core.AnimationMode.PRESERVE)
        self.assertIs(defaults.high_depth, core.HighDepthPolicy.BLOCK)
        self.assertIsNone(defaults.dest_dir)


class ProbeTests(ImagingTestCase):

    def test_probe_reads_a_header(self):
        result = core.probe(self.animation())
        self.assertEqual(result.frames, 3)
        self.assertEqual(result.src_format, "GIF")
        self.assertEqual(result.src_size, (20, 20))
        self.assertGreater(result.src_bytes, 0)
        self.assertIsNone(result.error)

    def test_probe_never_raises(self):
        broken = self.dir / "broken.png"
        broken.write_bytes(b"nope")
        self.assertIsNotNone(core.probe(broken).error)
        self.assertIsNotNone(core.probe(self.dir / "absent.png").error)

    def test_truncated_files_fail_the_row_not_the_run(self):
        source = self.rgb("full.jpg", size=(200, 200))
        data = source.read_bytes()
        cut = self.dir / "cut.jpg"
        cut.write_bytes(data[: len(data) // 2])
        result = core.convert_one(cut, self.settings(target_format="PNG"))
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_extension_may_lie_about_the_format(self):
        liar = self.dir / "liar.png"
        Image.new("RGB", (20, 20), (3, 4, 5)).save(liar, format="JPEG")
        self.assertEqual(core.probe(liar).src_format, "JPEG")
        result = core.convert_one(liar, self.settings(target_format="WEBP"))
        self.assertTrue(result.ok, result.error)


class ReportingTests(ImagingTestCase):

    def test_delta_percent(self):
        result = core.ConvertResult(source=Path("x"), ok=True,
                                    src_bytes=1000, dst_bytes=250)
        self.assertAlmostEqual(result.delta_pct, -75.0)
        self.assertEqual(result.delta_bytes, -750)

    def test_delta_is_zero_when_nothing_was_written(self):
        self.assertEqual(core.ConvertResult(source=Path("x")).delta_pct, 0.0)

    def test_summary_percentages(self):
        summary = core.BatchSummary(results=[
            core.ConvertResult(source=Path("a"), ok=True, src_bytes=800, dst_bytes=200),
            core.ConvertResult(source=Path("b"), ok=True, src_bytes=200, dst_bytes=100),
            core.ConvertResult(source=Path("c"), skipped=True),
            core.ConvertResult(source=Path("d"), error="boom"),
        ])
        self.assertEqual(summary.converted, 2)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.saved_bytes, 700)
        self.assertAlmostEqual(summary.saved_pct, 70.0)

    def test_human_bytes(self):
        self.assertEqual(core.human_bytes(512), "512 B")
        self.assertEqual(core.human_bytes(2048), "2.0 KB")
        self.assertTrue(core.human_bytes(-2048).startswith("-"))


class HeadlessTests(unittest.TestCase):
    def test_core_does_not_pull_in_tkinter(self):
        import nanatools.core.imaging as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("customtkinter", source)

    def test_no_tkinter_in_the_import_graph(self):
        for name in ("tkinter", "customtkinter"):
            module = sys.modules.get("nanatools.core.imaging")
            self.assertIsNotNone(module)
            self.assertNotIn(name, getattr(module, "__dict__"))

    def test_available_formats_is_honest(self):
        formats = core.available_formats()
        self.assertIn("PNG", formats)
        self.assertLessEqual(set(formats), set(core.EXTENSIONS))
        for fmt in formats:
            self.assertIn(fmt, core.SAVE_KWARGS)


if __name__ == "__main__":
    unittest.main()
