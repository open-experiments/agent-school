"""Agent-school course walkthrough video producer.

Composes a narrated MP4 from an ordered list of scenes. Each scene is
either a title card or a live RHOAI portal tab capture, framed 1080p with
a course banner, a step/tab chip, and a wrapped caption band that mirrors
the voice-over. Narration is neural TTS (Kokoro); assembly is ffmpeg.

A tab scene can list cuts. The narration is spoken once, and the
screenshot changes when the voice reaches each cut's phrase.

Finished scene clips are cached under the build directory. A later run
re-speaks only the scenes whose words, pictures, or step label changed,
then stitches the full video again.

Usage: python3 produce.py <course_spec.json>
The spec drives everything so all five courses share one pipeline.
"""
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FONT = "~/Library/Fonts/DejaVuSans.ttf"
FONT_B = "~/Library/Fonts/DejaVuSans-Bold.ttf"
FONT_M = "~/Library/Fonts/DejaVuSansMono.ttf"
# FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
# FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# FONT_M = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# Narration: Kokoro (offline neural), calm female voice by default.
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_sarah")
SR = 24000
_PIPE = None


def _pipe():
    global _PIPE
    if _PIPE is None:
        import warnings
        warnings.filterwarnings("ignore")
        from kokoro import KPipeline
        _PIPE = KPipeline(lang_code="a")  # American English
    return _PIPE

# palette (dark, RHOAI-adjacent)
BG = (13, 13, 15)
PANEL = (22, 23, 27)
BANNER = (20, 21, 26)
ACCENT = (238, 0, 0)      # red hat red
CHIP = (37, 99, 235)      # blue chip
INK = (233, 235, 240)
SUBINK = (150, 155, 165)
CAPBG = (17, 18, 22)

BUILD = Path(os.environ.get("BUILD_DIR", "/tmp/vidbuild"))
# Bump when framing, timing, or the voice pipeline changes so old clips are not reused.
CACHE_VERSION = 1
REBUILD = os.environ.get("PRODUCE_REBUILD", "") not in ("", "0")
DEFAULT_FOOTER = (
    "Agentic AI Stack · live on our DevOps cluster called Rome "
    "(RHOAI 3.5 EA · OpenShift 4.22 SNO)")


def font(path, size):
    return ImageFont.truetype(path, size)


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def frame_title(scene, course, idx, total):
    """A full-bleed problem-statement / section title card."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # accent bar
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.text((120, 150), course["banner"], font=font(FONT_B, 34), fill=SUBINK)
    # big title
    tf = font(FONT_B, 82)
    for i, line in enumerate(wrap(d, scene["title"], tf, W - 240)):
        d.text((120, 250 + i * 100), line, font=tf, fill=INK)
    # subtitle / problem
    sf = font(FONT, 40)
    y = 250 + len(wrap(d, scene["title"], tf, W - 240)) * 100 + 50
    for line in wrap(d, scene.get("subtitle", ""), sf, W - 300):
        d.text((120, y), line, font=sf, fill=(205, 208, 215))
        y += 58
    # footer chip
    d.text((120, H - 90), course.get("footer", DEFAULT_FOOTER),
           font=font(FONT, 28), fill=SUBINK)
    d.text((W - 240, H - 90), "%d / %d" % (idx, total),
           font=font(FONT_B, 30), fill=SUBINK)
    return img


def frame_tab(scene, course, idx, total, tab_total=None):
    """Two-column explainer: the portal capture fills the LEFT panel
    (tall captures fit here without side letterboxing), and the RIGHT
    panel — previously wasted margin — carries the step, tab name, and
    the narration caption. No dead space."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=ACCENT)

    # full-width slim banner
    d.text((40, 16), course["banner"], font=font(FONT_B, 26), fill=INK)
    top = 66

    # ---- left image panel ------------------------------------------
    LMARG, GUT = 28, 24
    right_w = 660
    left_x0 = LMARG
    left_x1 = W - right_w - GUT - LMARG
    lw = left_x1 - left_x0
    lh = H - top - 28
    shot = Image.open(scene["image"]).convert("RGB")
    sw, sh = shot.size
    scale = min(lw / sw, lh / sh, 1.25)
    nw, nh = int(sw * scale), int(sh * scale)
    shot = shot.resize((nw, nh), Image.LANCZOS)
    ox = left_x0 + (lw - nw) // 2
    oy = top + (lh - nh) // 2
    d.rectangle([ox - 4, oy - 4, ox + nw + 4, oy + nh + 4], fill=PANEL)
    img.paste(shot, (ox, oy))

    # ---- right narration panel -------------------------------------
    rx0 = left_x1 + GUT
    rx1 = W - LMARG
    d.rectangle([rx0, top, rx1, H - 28], fill=CAPBG)
    d.rectangle([rx0, top, rx0 + 6, H - 28], fill=CHIP)
    pad = 34
    tx = rx0 + pad
    tw = rx1 - rx0 - pad * 2

    # step chip
    chip = "STEP %s / %d" % (scene.get("step", idx), tab_total or total)
    cf = font(FONT_B, 24)
    cwid = d.textlength(chip, font=cf)
    d.rounded_rectangle([tx, top + 34, tx + cwid + 34, top + 82],
                        radius=10, fill=CHIP)
    d.text((tx + 17, top + 43), chip, font=cf, fill=(255, 255, 255))

    # tab name (heading)
    y = top + 118
    hf = font(FONT_B, 38)
    for line in wrap(d, scene.get("tab", ""), hf, tw):
        d.text((tx, y), line, font=hf, fill=INK)
        y += 48
    y += 18
    d.line([(tx, y), (tx + tw, y)], fill=(60, 63, 72), width=2)
    y += 30

    # narration caption body
    cap = scene.get("caption", scene.get("say", ""))
    bf = font(FONT, 32)
    for line in wrap(d, cap, bf, tw):
        d.text((tx, y), line, font=bf, fill=(222, 226, 233))
        y += 46
    return img


def synthesize(text):
    """Narration audio, the spoken text, and (char_index, seconds) marks.

    Spoken text is the narration after pronunciation markup is removed.
    Chunks are joined by the same short breath synth() has always used.
    """
    gap = np.zeros(int(SR * 0.18), dtype=np.float32)
    pieces = []
    spoken = []
    marks = []
    cursor_t = 0.0
    cursor_c = 0
    for result in _pipe()(text, voice=KOKORO_VOICE):
        audio = result.audio
        a = audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)
        a = np.asarray(a, dtype=np.float32)
        tokens = list(result.tokens or [])
        parts = [(t.text or "") + (t.whitespace or "") for t in tokens]
        chunk_dur = len(a) / SR
        timed = [t.start_ts is not None or not p for t, p in zip(tokens, parts)]
        if parts and all(timed):
            for t, p in zip(tokens, parts):
                marks.append((cursor_c, cursor_t + float(t.start_ts or 0.0)))
                cursor_c += len(p)
                spoken.append(p)
        else:
            total = sum(len(p) for p in parts) or 1
            acc = 0
            for p in parts:
                marks.append((cursor_c, cursor_t + chunk_dur * acc / total))
                cursor_c += len(p)
                acc += len(p)
                spoken.append(p)
        pieces.append(a)
        cursor_t += chunk_dur
        pieces.append(gap)
        cursor_t += len(gap) / SR
    if not pieces:
        raise RuntimeError("kokoro produced no audio")
    return np.concatenate(pieces), "".join(spoken), marks


def synth(text, out_wav):
    """Neural TTS with a short breath between sentence chunks for a calm,
    natural cadence."""
    audio, _, _ = synthesize(text)
    sf.write(out_wav, audio, SR)


def cue_time(spoken, marks, phrase):
    """Seconds at which phrase begins in the spoken narration."""
    idx = spoken.find(phrase)
    if idx < 0:
        raise RuntimeError(
            "cut phrase not found in the spoken narration: %r. "
            "Copy it from say, and leave out [word](/phonemes/) markup."
            % phrase)
    time = 0.0
    for c, t in marks:
        if c <= idx:
            time = t
        else:
            break
    return time


def image_spans(scene, spoken, marks, audio_len):
    """(start_sec, image) for the opening screenshot and each cut."""
    spans = [(0.0, scene["image"])]
    for cut in scene.get("cuts") or []:
        if "at" not in cut or "image" not in cut:
            raise RuntimeError('each cut needs "at" and "image"')
        spans.append((cue_time(spoken, marks, cut["at"]), cut["image"]))
    spans.sort(key=lambda s: s[0])
    cleaned = []
    for start, image in spans:
        if start >= audio_len:
            continue
        start = max(0.0, start)
        if cleaned and abs(start - cleaned[-1][0]) < 1 / 25:
            cleaned[-1] = (cleaned[-1][0], image)
            continue
        cleaned.append((start, image))
    if not cleaned:
        cleaned = [(0.0, scene["image"])]
    return cleaned


def dur(wav):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", wav], capture_output=True, text=True).stdout.strip()
    return float(out)


def clip(png, wav, out_mp4, pad=0.6):
    """One scene: still image for the narration duration + tail pad."""
    total = dur(wav) + pad
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", png, "-i", wav,
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-t", "%.3f" % total,
        "-r", "25", "-vf", "scale=1920:1080", out_mp4],
        check=True, capture_output=True)


def clip_spans(spans, wav, out_mp4, pad=0.6):
    """One narration, several stills. spans is (start_sec, png).

    The last still holds through the same tail pad clip() adds. There is
    no pad between stills, so the voice does not pause when the picture changes.
    """
    end_frame = max(1, int(round((dur(wav) + pad) * 25)))
    bounds = []
    for n, (start, png) in enumerate(spans):
        frame = 0 if n == 0 else min(end_frame - 1, int(round(start * 25)))
        if bounds and frame <= bounds[-1][0]:
            bounds[-1] = (bounds[-1][0], png)
            continue
        bounds.append((frame, png))
    lines = []
    stem = Path(out_mp4).stem
    for n, (frame, png) in enumerate(bounds):
        nxt = bounds[n + 1][0] if n + 1 < len(bounds) else end_frame
        nframes = nxt - frame
        if nframes <= 0:
            continue
        seg = str(BUILD / ("seg-%s-%02d.mp4" % (stem, n)))
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-framerate", "25", "-i", png,
            "-frames:v", str(nframes), "-r", "25", "-vf", "scale=1920:1080",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-an", seg], check=True, capture_output=True)
        lines.append("file '%s'\n" % seg)
    listf = BUILD / ("vlist-%s.txt" % stem)
    listf.write_text("".join(lines))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
        "-i", wav, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", "apad",
        "-t", "%.3f" % (end_frame / 25), out_mp4],
        check=True, capture_output=True)


def _need(errors, ok, where, what):
    if not ok:
        errors.append("%s: %s" % (where, what))


def _image_ok(path):
    """None when path is a readable image, otherwise a short reason."""
    p = Path(path)
    if not p.is_file():
        return "file not found"
    try:
        with Image.open(p) as im:
            im.verify()
    except Exception as e:
        return "not a readable image (%s)" % e
    return None


def validate_spec(spec):
    """Structural checks and screenshot existence. Empty means the spec can build."""
    errors = []
    if not isinstance(spec, dict):
        return ["top level must be a JSON object"]
    output = spec.get("output")
    _need(errors, isinstance(output, str) and output.strip(), "output",
          "must be the path of the finished mp4")
    course = spec.get("course")
    _need(errors, isinstance(course, dict), "course", "must be an object")
    if isinstance(course, dict):
        banner = course.get("banner")
        _need(errors, isinstance(banner, str) and banner.strip(),
              "course.banner", "must be a non-empty string")
    scenes = spec.get("scenes")
    _need(errors, isinstance(scenes, list) and scenes, "scenes",
          "must be a non-empty list")
    if not isinstance(scenes, list):
        return errors
    images = []
    for n, sc in enumerate(scenes, 1):
        where = "scene %d" % n
        if not isinstance(sc, dict):
            errors.append("%s: must be an object" % where)
            continue
        kind = sc.get("type")
        _need(errors, kind in ("title", "tab"), where,
              'type must be "title" or "tab"')
        say = sc.get("say")
        _need(errors, isinstance(say, str) and say.strip(), where,
              "say must be a non-empty string")
        if kind == "title":
            title = sc.get("title")
            _need(errors, isinstance(title, str) and title.strip(), where,
                  "title must be a non-empty string")
            if sc.get("cuts"):
                errors.append("%s: a title scene cannot have cuts" % where)
        elif kind == "tab":
            image = sc.get("image")
            _need(errors, isinstance(image, str) and image.strip(), where,
                  "image must be a path")
            if isinstance(image, str) and image.strip():
                images.append((where, image))
        cuts = sc.get("cuts")
        if cuts is None:
            continue
        if not isinstance(cuts, list):
            errors.append("%s: cuts must be a list" % where)
            continue
        for i, cut in enumerate(cuts, 1):
            clabel = "%s cut %d" % (where, i)
            if not isinstance(cut, dict):
                errors.append("%s: must be an object" % clabel)
                continue
            phrase = cut.get("at")
            _need(errors, isinstance(phrase, str) and phrase.strip(), clabel,
                  'at must be a phrase copied from say')
            if isinstance(phrase, str) and isinstance(say, str) and phrase not in say:
                errors.append("%s: %r is not in say" % (clabel, phrase))
            image = cut.get("image")
            _need(errors, isinstance(image, str) and image.strip(), clabel,
                  "image must be a path")
            if isinstance(image, str) and image.strip():
                images.append((clabel, image))
    for label, path in images:
        reason = _image_ok(path)
        if reason:
            errors.append("%s: %s: %s" % (label, reason, path))
    return errors


def load_spec(path):
    """Read a course spec, or exit before any frames or narration are built."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit("spec not found: %s" % path)
    try:
        spec = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit("invalid JSON in %s: %s" % (path, e))
    errors = validate_spec(spec)
    if errors:
        raise SystemExit("invalid spec %s\n%s" % (
            path, "\n".join("  " + e for e in errors)))
    nimg = sum(1 for sc in spec["scenes"] if sc.get("type") == "tab")
    nimg += sum(len(sc.get("cuts") or []) for sc in spec["scenes"])
    print("spec ok: %d scenes, %d images" % (len(spec["scenes"]), nimg),
          flush=True)
    return spec


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _add(h, text):
    h.update(str(text).encode("utf-8"))
    h.update(b"\0")


def scene_fingerprint(scene, course, idx, total, tab_total):
    """Hash of every input that changes this scene's picture or narration."""
    h = hashlib.sha256()
    _add(h, CACHE_VERSION)
    _add(h, KOKORO_VOICE)
    _add(h, scene.get("type"))
    _add(h, scene.get("say", ""))
    _add(h, scene.get("title", ""))
    _add(h, scene.get("subtitle", ""))
    _add(h, scene.get("tab", ""))
    caption = scene["caption"] if "caption" in scene else scene.get("say", "")
    _add(h, caption)
    _add(h, course.get("banner", ""))
    _add(h, course.get("footer", DEFAULT_FOOTER))
    if scene.get("type") == "title":
        _add(h, idx)
        _add(h, total)
    else:
        _add(h, scene.get("step", idx))
        _add(h, tab_total or total)
    images = []
    if scene.get("image"):
        images.append(scene["image"])
    for cut in scene.get("cuts") or []:
        _add(h, cut.get("at", ""))
        _add(h, cut.get("image", ""))
        if cut.get("image"):
            images.append(cut["image"])
    for path in images:
        _add(h, path)
        _add(h, _sha256(path))
    for path in (FONT, FONT_B, FONT_M):
        _add(h, path)
        font_file = Path(path).expanduser()
        if font_file.is_file():
            _add(h, _sha256(font_file))
    return h.hexdigest()


def build_scene(sc, course, i, total, tab_total, mp4):
    """Render one scene mp4. Returns the narration length in seconds, without the tail pad."""
    png = str(BUILD / ("f%02d.png" % i))
    wav = str(BUILD / ("a%02d.wav" % i))
    if sc["type"] == "title" or not sc.get("cuts"):
        fr = frame_title(sc, course, i, total) if sc["type"] == "title" \
            else frame_tab(sc, course, i, total, tab_total)
        fr.save(png)
        synth(sc["say"], wav)
        clip(png, wav, mp4)
    else:
        audio, spoken, marks = synthesize(sc["say"])
        sf.write(wav, audio, SR)
        spans = image_spans(sc, spoken, marks, dur(wav))
        framed = []
        for n, (start, image) in enumerate(spans):
            shot = dict(sc)
            shot["image"] = image
            fp = str(BUILD / ("f%02d-%02d.png" % (i, n)))
            frame_tab(shot, course, i, total, tab_total).save(fp)
            framed.append((start, fp))
            if n:
                print("  cut %5.1fs  %s" % (start, Path(image).name),
                      flush=True)
        clip_spans(framed, wav, mp4)
    return dur(wav)


def main(spec_path):
    spec = load_spec(spec_path)
    course = spec["course"]
    scenes = spec["scenes"]
    BUILD.mkdir(parents=True, exist_ok=True)
    cache = BUILD / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    total = len(scenes)
    tab_total = sum(1 for x in scenes if x["type"] != "title")
    clips = []
    rebuilt = 0
    for i, sc in enumerate(scenes, 1):
        label = sc.get("tab", sc.get("title", ""))[:50]
        key = scene_fingerprint(sc, course, i, total, tab_total)
        mp4 = cache / ("%s.mp4" % key)
        meta = cache / ("%s.json" % key)
        seconds = None
        if not REBUILD and mp4.is_file() and meta.is_file():
            try:
                seconds = float(json.loads(meta.read_text())["seconds"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                seconds = None
        if seconds is None:
            partial = cache / ("%s.partial.mp4" % key)
            seconds = build_scene(sc, course, i, total, tab_total, str(partial))
            partial.replace(mp4)
            meta.write_text(json.dumps({"seconds": seconds}))
            rebuilt += 1
            print("scene %d/%d  %5.1fs  %s" % (i, total, seconds, label),
                  flush=True)
        else:
            print("scene %d/%d  cached %5.1fs  %s" % (i, total, seconds, label),
                  flush=True)
        clips.append(str(mp4))
    keep = set()
    for clip_path in clips:
        keep.add(Path(clip_path).name)
        keep.add(Path(clip_path).with_suffix(".json").name)
    for stale in cache.iterdir():
        if stale.name not in keep:
            stale.unlink()
    print("scenes rendered %d, reused %d" % (rebuilt, total - rebuilt),
          flush=True)
    listf = BUILD / "list.txt"
    listf.write_text("".join("file '%s'\n" % c for c in clips))
    out = spec["output"]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(listf), "-c", "copy", out],
                   check=True, capture_output=True)
    d = dur(out)
    print("DONE %s  %.0fs (%d:%02d)" % (out, d, int(d // 60), int(d % 60)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 produce.py <course_spec.json>")
    main(sys.argv[1])
