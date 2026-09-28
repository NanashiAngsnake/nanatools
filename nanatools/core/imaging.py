from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PIL import Image, ImageOps, ImageSequence, features


FORMAT_MODES: dict[str, set[str] | None] = {
    "JPEG": {"1", "L", "RGB", "CMYK"},
    "PNG": {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"},
    "WEBP": None,
    "AVIF": None,
    "BMP": {"1", "L", "P", "RGB", "RGBA"},
    "TIFF": None,
    "GIF": {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"},
    "ICO": {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"},
}

EXTENSIONS: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "AVIF": ".avif",
    "BMP": ".bmp",
    "TIFF": ".tif",
    "GIF": ".gif",
    "ICO": ".ico",
}

ANIMATABLE = {"GIF", "WEBP", "AVIF", "PNG"}

ALPHA_CAPABLE = {"PNG", "WEBP", "AVIF", "TIFF", "GIF", "ICO", "BMP"}

ALPHA_MODES = {"RGBA", "LA", "PA", "La"}
HIGH_DEPTH_MODES = {"I", "I;16", "I;16B", "I;16L", "F"}

HIGH_DEPTH_SAFE = {"PNG", "TIFF"}

WEBP_MAX_DIMENSION = 16383

TRUE_LOSSLESS = ("PNG", "WEBP (lossless)", "TIFF")

SAVE_KWARGS: dict[str, set[str]] = {
    "JPEG": {"quality", "optimize", "progressive", "subsampling", "exif",
             "icc_profile", "dpi"},
    "PNG": {"optimize", "compress_level", "exif", "icc_profile", "save_all",
            "append_images", "duration", "loop", "transparency", "default_image"},
    "WEBP": {"quality", "lossless", "method", "exif", "icc_profile", "save_all",
             "append_images", "duration", "loop", "minimize_size"},
    "AVIF": {"quality", "speed", "subsampling", "range", "codec", "max_threads",
             "autotiling", "tile_rows", "tile_cols", "alpha_premultiplied",
             "advanced", "exif", "xmp", "icc_profile", "save_all",
             "append_images", "duration"},
    "BMP": set(),
    "TIFF": {"compression", "dpi", "exif", "icc_profile", "save_all",
             "append_images"},
    "GIF": {"save_all", "append_images", "duration", "loop", "disposal",
            "optimize", "transparency"},
    "ICO": {"sizes", "bitmap_format"},
}

INPUT_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".webp", ".avif", ".bmp", ".dib",
    ".tif", ".tiff", ".gif", ".ico", ".jp2", ".j2k", ".ppm", ".pgm", ".pnm",
    ".tga", ".apng",
})

RESAMPLERS: dict[str, int] = {
    "LANCZOS": Image.Resampling.LANCZOS,
    "BICUBIC": Image.Resampling.BICUBIC,
    "BILINEAR": Image.Resampling.BILINEAR,
    "HAMMING": Image.Resampling.HAMMING,
    "BOX": Image.Resampling.BOX,
    "NEAREST": Image.Resampling.NEAREST,
}

AVIF_MAX_WORKERS = 4
AVIF_MAX_THREADS = 4


def available_formats() -> list[str]:
    out = []
    for fmt in EXTENSIONS:
        if fmt == "AVIF" and not features.check("avif"):
            continue
        if fmt == "WEBP" and not features.check("webp"):
            continue
        out.append(fmt)
    return out


def set_pixel_limit(max_pixels: int | None) -> None:
    Image.MAX_IMAGE_PIXELS = max_pixels


class ResizeMode(str, Enum):
    NONE = "none"
    FIT = "fit"
    LONG_EDGE = "long_edge"
    PERCENT = "percent"
    EXACT = "exact"


class MetadataPolicy(str, Enum):
    KEEP = "keep"
    COLOUR_ONLY = "colour"
    STRIP = "strip"


class AnimationMode(str, Enum):
    PRESERVE = "preserve"
    FIRST_FRAME = "first"
    SKIP = "skip"


class OnCollision(str, Enum):
    RENAME = "rename"
    SKIP = "skip"
    OVERWRITE = "overwrite"


class HighDepthPolicy(str, Enum):
    BLOCK = "block"
    DOWNCAST = "downcast"


@dataclass(frozen=True)
class ConvertSettings:
    target_format: str = "WEBP"
    dest_dir: Path | None = None

    resize_mode: ResizeMode = ResizeMode.NONE
    max_w: int = 1920
    max_h: int = 1920
    long_edge: int = 1920
    percent: float = 100.0
    allow_upscale: bool = False
    resample: str = "LANCZOS"

    quality: int = 82
    webp_lossless: bool = False
    webp_method: int = 4
    avif_speed: int = 6
    jpeg_progressive: bool = True
    jpeg_optimize: bool = True
    png_compress_level: int = 6
    tiff_compression: str = "tiff_lzw"
    ico_sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)

    metadata: MetadataPolicy = MetadataPolicy.KEEP
    flatten_bg: tuple[int, int, int] = (255, 255, 255)
    animation: AnimationMode = AnimationMode.PRESERVE
    on_collision: OnCollision = OnCollision.RENAME
    high_depth: HighDepthPolicy = HighDepthPolicy.BLOCK
    workers: int = 0


    def to_dict(self) -> dict:
        data: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Enum):
                data[key] = value.value
            elif isinstance(value, Path):
                data[key] = str(value)
            elif isinstance(value, tuple):
                data[key] = list(value)
            else:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ConvertSettings":
        enums = {
            "resize_mode": ResizeMode, "metadata": MetadataPolicy,
            "animation": AnimationMode, "on_collision": OnCollision,
            "high_depth": HighDepthPolicy,
        }
        kwargs: dict[str, Any] = {}
        defaults = cls()
        for key in defaults.__dict__:
            if key not in data:
                continue
            value = data[key]
            try:
                if key in enums:
                    kwargs[key] = enums[key](value)
                elif key == "dest_dir":
                    kwargs[key] = Path(value) if value else None
                elif key in ("ico_sizes", "flatten_bg"):
                    kwargs[key] = tuple(value)
                else:
                    kwargs[key] = type(getattr(defaults, key))(value)
            except (TypeError, ValueError):
                continue
        return cls(**kwargs)

    def with_(self, **changes: Any) -> "ConvertSettings":
        return replace(self, **changes)

    @property
    def resample_filter(self) -> int:
        return RESAMPLERS.get(self.resample, Image.Resampling.LANCZOS)


@dataclass
class ConvertResult:
    source: Path
    target: Path | None = None
    ok: bool = False
    skipped: bool = False
    src_bytes: int = 0
    dst_bytes: int = 0
    src_size: tuple[int, int] | None = None
    dst_size: tuple[int, int] | None = None
    src_mode: str = ""
    src_format: str = ""
    frames: int = 1
    out_frames: int = 1
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def delta_bytes(self) -> int:
        return self.dst_bytes - self.src_bytes if self.ok else 0

    @property
    def delta_pct(self) -> float:
        if not (self.ok and self.src_bytes):
            return 0.0
        return (self.dst_bytes - self.src_bytes) / self.src_bytes * 100.0


@dataclass
class BatchSummary:
    results: list[ConvertResult] = field(default_factory=list)
    cancelled: bool = False

    @property
    def converted(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok and not r.skipped)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)

    @property
    def total_src_bytes(self) -> int:
        return sum(r.src_bytes for r in self.results if r.ok)

    @property
    def total_dst_bytes(self) -> int:
        return sum(r.dst_bytes for r in self.results if r.ok)

    @property
    def saved_bytes(self) -> int:
        return self.total_src_bytes - self.total_dst_bytes

    @property
    def saved_pct(self) -> float:
        total = self.total_src_bytes
        return (self.saved_bytes / total * 100.0) if total else 0.0


class UnsupportedSource(Exception):
    pass


def human_bytes(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{sign}{n:.0f} {unit}" if unit == "B" else f"{sign}{n:.1f} {unit}"
        n /= 1024.0
    return f"{sign}{n:.1f} GB"


def discover(
    paths: Iterable[Path | str],
    *,
    recursive: bool = False,
    extensions: Iterable[str] | None = None,
) -> list[Path]:
    wanted = {e.lower() if e.startswith(".") else "." + e.lower()
              for e in (extensions or INPUT_EXTENSIONS)}
    found: list[Path] = []
    seen: set[str] = set()

    def take(p: Path) -> None:
        if p.suffix.lower() not in wanted:
            return
        key = str(p.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(p)

    for raw in paths:
        p = Path(str(raw).strip().strip('"'))
        if not p.exists():
            continue
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            for child in it:
                if child.is_file():
                    take(child)
        elif p.is_file():
            take(p)
    return sorted(found, key=lambda q: str(q).lower())


def probe(path: Path) -> ConvertResult:
    result = ConvertResult(source=Path(path))
    try:
        result.src_bytes = os.path.getsize(path)
    except OSError as exc:
        result.error = str(exc)
        return result
    try:
        with Image.open(path) as im:
            result.src_size = im.size
            result.src_mode = im.mode
            result.src_format = im.format or ""
            result.frames = int(getattr(im, "n_frames", 1) or 1)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def orientation_transpose(im: Image.Image) -> int | None:
    try:
        tag = im.getexif().get(0x0112, 1)
    except Exception:
        return None
    return {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(tag)


def upright(im: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(im) or im


def exif_bytes_after_transpose(im: Image.Image) -> bytes | None:
    try:
        exif = im.getexif()
    except Exception:
        return None
    if not exif:
        return None
    exif.pop(0x0112, None)
    try:
        return exif.tobytes()
    except Exception:
        return None


def flatten(im: Image.Image, bg: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    mode = im.mode
    if mode == "P":
        if "transparency" not in im.info:
            return im.convert("RGB")
        im = im.convert("RGBA")
    elif mode == "PA":
        im = im.convert("RGBA")
    elif mode == "LA":
        im = im.convert("RGBA")
    elif mode not in ("RGBA", "La"):
        return im if mode == "RGB" else im.convert("RGB")

    if im.mode == "La":
        im = im.convert("RGBA")
    canvas = Image.new("RGB", im.size, tuple(bg))
    canvas.paste(im, mask=im.getchannel("A"))
    return canvas


def downcast_high_depth(im: Image.Image) -> Image.Image:
    if im.mode in ("I;16", "I;16B", "I;16L"):
        im = im.convert("I")
    if im.mode == "I":
        return im.point(lambda v: v * (1.0 / 256.0)).convert("L")
    if im.mode == "F":
        lo, hi = im.getextrema()
        span = (hi - lo) or 1.0
        return im.point(lambda v: (v - lo) * (255.0 / span)).convert("L")
    return im


def coerce_mode(
    im: Image.Image,
    fmt: str,
    bg: tuple[int, int, int] = (255, 255, 255),
) -> tuple[Image.Image, list[str]]:
    allowed = FORMAT_MODES.get(fmt)
    notes: list[str] = []

    if allowed is None:
        if im.mode in HIGH_DEPTH_MODES and fmt not in HIGH_DEPTH_SAFE:
            return downcast_high_depth(im), ["rescaled to 8-bit"]
        return im, notes

    if im.mode in allowed:
        return im, notes

    has_alpha = im.mode in ALPHA_MODES or (im.mode == "P" and "transparency" in im.info)

    if im.mode in HIGH_DEPTH_MODES:
        im = downcast_high_depth(im)
        notes.append("rescaled to 8-bit")
        if im.mode in allowed:
            return im, notes

    if has_alpha:
        if "RGBA" in allowed:
            return im.convert("RGBA"), notes
        im = flatten(im, bg)
        notes.append(f"transparency flattened onto #{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}")
        if im.mode in allowed:
            return im, notes

    for candidate in ("RGB", "L", "P", "1"):
        if candidate in allowed:
            if im.mode != candidate:
                im = im.convert(candidate)
                if candidate in ("L", "1"):
                    notes.append(f"converted to {candidate}")
            return im, notes
    return im, notes


def target_size(im_size: tuple[int, int], s: ConvertSettings) -> tuple[int, int]:
    w, h = im_size
    mode = s.resize_mode
    if mode is ResizeMode.NONE or w <= 0 or h <= 0:
        return w, h

    if mode is ResizeMode.PERCENT:
        scale = max(s.percent, 0.01) / 100.0
        if not s.allow_upscale:
            scale = min(scale, 1.0)
    elif mode is ResizeMode.EXACT:
        nw = max(1, int(s.max_w))
        nh = max(1, int(s.max_h))
        if not s.allow_upscale:
            nw, nh = min(nw, w), min(nh, h)
        return nw, nh
    else:
        if mode is ResizeMode.LONG_EDGE:
            edge = max(1, int(s.long_edge))
            box_w = box_h = edge
        else:
            box_w, box_h = max(1, int(s.max_w)), max(1, int(s.max_h))
        scale = min(box_w / w, box_h / h)
        if not s.allow_upscale:
            scale = min(scale, 1.0)

    return max(1, round(w * scale)), max(1, round(h * scale))


def fit(
    im: Image.Image,
    max_w: int,
    max_h: int,
    *,
    allow_upscale: bool = False,
    resample: int = Image.Resampling.LANCZOS,
) -> Image.Image:
    size = target_size(im.size, ConvertSettings(
        resize_mode=ResizeMode.FIT, max_w=max_w, max_h=max_h,
        allow_upscale=allow_upscale,
    ))
    return im if size == im.size else im.resize(size, resample)


def ico_sizes_for(size: tuple[int, int], wanted: Sequence[int]) -> list[tuple[int, int]]:
    w, h = size
    cap = min(256, w, h)
    sizes = sorted({int(n) for n in wanted if 0 < int(n) <= cap})
    if not sizes:
        return [(min(w, 256), min(h, 256))]
    return [(n, n) for n in sizes]


def build_save_kwargs(
    fmt: str,
    s: ConvertSettings,
    *,
    icc: bytes | None = None,
    exif: bytes | None = None,
    size: tuple[int, int] | None = None,
) -> dict:
    fmt = fmt.upper()
    if fmt not in SAVE_KWARGS:
        raise ValueError(f"unsupported target format: {fmt}")

    kw: dict[str, Any] = {}
    quality = int(max(1, min(100, s.quality)))

    if fmt == "JPEG":
        kw.update(quality=quality, optimize=bool(s.jpeg_optimize),
                  progressive=bool(s.jpeg_progressive))
    elif fmt == "PNG":
        kw.update(optimize=True, compress_level=int(max(0, min(9, s.png_compress_level))))
    elif fmt == "WEBP":
        kw.update(quality=quality, method=int(max(0, min(6, s.webp_method))))
        if s.webp_lossless:
            kw["lossless"] = True
    elif fmt == "AVIF":
        kw.update(quality=quality, speed=int(max(0, min(10, s.avif_speed))),
                  max_threads=AVIF_MAX_THREADS)
        if quality >= 90:
            kw["subsampling"] = "4:4:4"
    elif fmt == "TIFF":
        kw["compression"] = s.tiff_compression
    elif fmt == "GIF":
        kw["optimize"] = True
    elif fmt == "ICO":
        kw["sizes"] = ico_sizes_for(size or (256, 256), s.ico_sizes)

    allowed = SAVE_KWARGS[fmt]
    if icc and "icc_profile" in allowed:
        kw["icc_profile"] = icc
    if exif and "exif" in allowed:
        kw["exif"] = exif

    unknown = set(kw) - allowed
    if unknown:
        raise ValueError(
            f"{fmt} would silently ignore these save options: {sorted(unknown)}"
        )
    return kw


def metadata_gap(fmt: str, *, icc: bytes | None, exif: bytes | None) -> list[str]:
    allowed = SAVE_KWARGS.get(fmt.upper(), frozenset())
    gaps: list[str] = []
    if icc and "icc_profile" not in allowed:
        gaps.append(f"{fmt} cannot carry a colour profile; ICC dropped")
    if exif and "exif" not in allowed:
        gaps.append(f"{fmt} cannot carry EXIF; metadata dropped")
    return gaps


_SUFFIXED = re.compile(r"^(?P<stem>.*?) \((?P<n>\d+)\)$")


def _unique(path: Path, reserved: set[str]) -> Path:
    stem, suffix = path.stem, path.suffix
    match = _SUFFIXED.match(stem)
    if match:
        stem = match.group("stem")
    candidate = path
    index = 0
    while str(candidate).lower() in reserved or candidate.exists():
        index += 1
        candidate = path.with_name(f"{stem} ({index}){suffix}")
    return candidate


def dest_for(src: Path, s: ConvertSettings) -> Path:
    folder = Path(s.dest_dir) if s.dest_dir else src.parent / "converted"
    return folder / (src.stem + EXTENSIONS[s.target_format.upper()])


def plan_targets(
    sources: Sequence[Path],
    s: ConvertSettings,
) -> list[tuple[Path, Path | None, str | None]]:
    reserved: set[str] = set()
    plans: list[tuple[Path, Path | None, str | None]] = []
    for src in sources:
        src = Path(src)
        target = dest_for(src, s)
        try:
            same = os.path.normcase(str(target.resolve())) == os.path.normcase(
                str(src.resolve()))
        except OSError:
            same = False
        if same:
            plans.append((src, None, "output would overwrite the source"))
            continue

        key = str(target).lower()
        if s.on_collision is OnCollision.OVERWRITE:
            if key in reserved:
                target = _unique(target, reserved)
        elif s.on_collision is OnCollision.SKIP:
            if key in reserved or target.exists():
                plans.append((src, None, "a file with that name is already there"))
                continue
        else:
            target = _unique(target, reserved)

        reserved.add(str(target).lower())
        plans.append((src, target, None))
    return plans


class _Stop(Exception):
    pass


def _frames_of(im: Image.Image) -> list[Image.Image]:
    return [frame.copy() for frame in ImageSequence.Iterator(im)]


def _normalised_frames(frames: list[Image.Image]) -> list[Image.Image]:
    wants_alpha = any(
        f.mode in ALPHA_MODES or (f.mode == "P" and "transparency" in f.info)
        for f in frames
    )
    common = "RGBA" if wants_alpha else "RGB"
    return [f if f.mode == common else f.convert(common) for f in frames]


def _durations_of(im: Image.Image, count: int, fallback: int = 100) -> list[int]:
    durations: list[int] = []
    for index in range(count):
        value = None
        try:
            im.seek(index)
            value = im.info.get("duration")
        except Exception:
            pass
        durations.append(int(value) if value else fallback)
    try:
        im.seek(0)
    except Exception:
        pass
    return durations


def _prepare(
    frame: Image.Image,
    s: ConvertSettings,
    fmt: str,
    size: tuple[int, int],
    notes: list[str],
) -> Image.Image:
    if frame.size != size:
        frame = frame.resize(size, s.resample_filter)
    frame, mode_notes = coerce_mode(frame, fmt, s.flatten_bg)
    for note in mode_notes:
        if note not in notes:
            notes.append(note)
    return frame


def _verify(path: Path) -> None:
    with Image.open(path) as check:
        check.load()


def _write(im: Image.Image, target: Path, fmt: str, kwargs: dict,
           extra: list[Image.Image] | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".nanatools-tmp")
    try:
        if extra is not None:
            im.save(tmp, format=fmt, append_images=extra, **kwargs)
        else:
            im.save(tmp, format=fmt, **kwargs)
        _verify(tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def convert_one(
    src: Path,
    s: ConvertSettings,
    target: Path | None = None,
) -> ConvertResult:
    src = Path(src)
    fmt = s.target_format.upper()
    result = probe(src)
    if result.error:
        return result
    if target is None:
        plans = plan_targets([src], s)
        _, target, reason = plans[0]
        if target is None:
            result.skipped = True
            result.error = reason
            return result
    result.target = target

    try:
        with Image.open(src) as im:
            icc = im.info.get("icc_profile")
            animated = bool(getattr(im, "is_animated", False)) and result.frames > 1

            if im.mode in HIGH_DEPTH_MODES and fmt not in HIGH_DEPTH_SAFE:
                if s.high_depth is HighDepthPolicy.BLOCK:
                    raise UnsupportedSource(
                        f"{im.mode} source would be clipped to 8-bit by {fmt}; "
                        "convert to PNG or TIFF, or allow down-casting"
                    )
                result.warnings.append("rescaled to 8-bit")

            if animated and s.animation is AnimationMode.SKIP:
                result.skipped = True
                result.error = f"animated ({result.frames} frames), skipped by choice"
                return result

            keep_anim = (
                animated
                and s.animation is AnimationMode.PRESERVE
                and fmt in ANIMATABLE
            )
            if animated and not keep_anim:
                result.warnings.append(
                    f"animation dropped, first of {result.frames} frames written"
                )

            if keep_anim:
                return _convert_animated(im, src, target, s, fmt, icc, result)
            return _convert_still(im, src, target, s, fmt, icc, result)

    except UnsupportedSource as exc:
        result.ok = False
        result.error = str(exc)
    except Exception as exc:
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _guard_dimensions(fmt: str, size: tuple[int, int]) -> None:
    if fmt == "WEBP" and max(size) > WEBP_MAX_DIMENSION:
        raise UnsupportedSource(
            f"WEBP cannot store a side larger than {WEBP_MAX_DIMENSION} px "
            f"(this would be {size[0]}x{size[1]}); resize it or pick PNG"
        )


def _convert_still(
    im: Image.Image, src: Path, target: Path, s: ConvertSettings,
    fmt: str, icc: bytes | None, result: ConvertResult,
) -> ConvertResult:
    size = target_size(im.size, s)
    if im.format == "JPEG" and size != im.size:
        try:
            im.draft(None, size)
        except Exception:
            pass

    upright_im = upright(im)
    if upright_im.size != im.size:
        size = target_size(upright_im.size, s)
    _guard_dimensions(fmt, size)

    exif = None
    if s.metadata is MetadataPolicy.KEEP:
        exif = exif_bytes_after_transpose(upright_im)
    if s.metadata is MetadataPolicy.STRIP:
        icc = None

    out = _prepare(upright_im, s, fmt, size, result.warnings)
    result.warnings.extend(metadata_gap(fmt, icc=icc, exif=exif))
    kwargs = build_save_kwargs(fmt, s, icc=icc, exif=exif, size=out.size)
    _write(out, target, fmt, kwargs)

    result.ok = True
    result.dst_size = out.size
    result.out_frames = 1
    result.dst_bytes = os.path.getsize(target)
    return result


def _convert_animated(
    im: Image.Image, src: Path, target: Path, s: ConvertSettings,
    fmt: str, icc: bytes | None, result: ConvertResult,
) -> ConvertResult:
    method = orientation_transpose(im)
    durations = _durations_of(im, result.frames)
    loop = int(im.info.get("loop", 0) or 0)

    frames = _normalised_frames(_frames_of(im))
    base_size = frames[0].size if not method else frames[0].transpose(method).size
    size = target_size(base_size, s)
    _guard_dimensions(fmt, size)

    prepared: list[Image.Image] = []
    for frame in frames:
        if method is not None:
            frame = frame.transpose(method)
        prepared.append(_prepare(frame, s, fmt, size, result.warnings))

    exif = None
    if s.metadata is MetadataPolicy.KEEP:
        exif = exif_bytes_after_transpose(im)
    if s.metadata is MetadataPolicy.STRIP:
        icc = None

    result.warnings.extend(metadata_gap(fmt, icc=icc, exif=exif))
    kwargs = build_save_kwargs(fmt, s, icc=icc, exif=exif, size=size)
    kwargs["save_all"] = True
    kwargs["duration"] = durations
    if "loop" in SAVE_KWARGS[fmt]:
        kwargs["loop"] = loop
    unknown = set(kwargs) - SAVE_KWARGS[fmt]
    if unknown:
        raise ValueError(f"{fmt} would silently ignore: {sorted(unknown)}")

    _write(prepared[0], target, fmt, kwargs, extra=prepared[1:])

    result.ok = True
    result.dst_size = size
    result.dst_bytes = os.path.getsize(target)
    try:
        with Image.open(target) as written:
            result.out_frames = int(getattr(written, "n_frames", 1) or 1)
    except Exception:
        result.out_frames = len(prepared)
    return result


def worker_count(s: ConvertSettings, total: int) -> int:
    if s.workers > 0:
        want = s.workers
    else:
        want = min(12, (os.cpu_count() or 4))
    if s.target_format.upper() == "AVIF":
        want = min(want, AVIF_MAX_WORKERS)
    return max(1, min(want, max(1, total)))


def convert_batch(
    sources: Sequence[Path],
    s: ConvertSettings,
    *,
    on_result: Callable[[ConvertResult, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BatchSummary:
    plans = plan_targets([Path(p) for p in sources], s)
    summary = BatchSummary()
    total = len(plans)
    if not total:
        return summary

    cancelled = should_cancel or (lambda: False)
    done = 0

    def announce(result: ConvertResult) -> None:
        nonlocal done
        done += 1
        summary.results.append(result)
        if on_result is not None:
            on_result(result, done, total)

    def work(plan: tuple[Path, Path | None, str | None]) -> ConvertResult:
        src, target, reason = plan
        if cancelled():
            raise _Stop()
        if target is None:
            skipped = probe(src)
            skipped.skipped = True
            skipped.error = reason
            return skipped
        return convert_one(src, s, target)

    workers = worker_count(s, total)
    if workers == 1:
        for plan in plans:
            if cancelled():
                summary.cancelled = True
                break
            try:
                announce(work(plan))
            except _Stop:
                summary.cancelled = True
                break
        return summary

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, plan): plan for plan in plans}
        for future in list(futures):
            try:
                announce(future.result())
            except _Stop:
                summary.cancelled = True
            except Exception as exc:
                src = futures[future][0]
                announce(ConvertResult(source=src, error=f"{type(exc).__name__}: {exc}"))
    if cancelled():
        summary.cancelled = True
    return summary


__all__ = [
    "ConvertSettings", "ConvertResult", "BatchSummary", "UnsupportedSource",
    "ResizeMode", "MetadataPolicy", "AnimationMode", "OnCollision",
    "HighDepthPolicy",
    "convert_one", "convert_batch", "probe", "discover", "plan_targets",
    "dest_for", "build_save_kwargs", "metadata_gap", "coerce_mode", "flatten", "fit",
    "target_size", "ico_sizes_for", "downcast_high_depth", "upright",
    "exif_bytes_after_transpose", "orientation_transpose", "worker_count",
    "available_formats", "set_pixel_limit", "human_bytes",
    "EXTENSIONS", "FORMAT_MODES", "SAVE_KWARGS", "RESAMPLERS", "ANIMATABLE",
    "INPUT_EXTENSIONS", "TRUE_LOSSLESS", "WEBP_MAX_DIMENSION",
]
