"""Agent-school course walkthrough video producer.

Composes a narrated MP4 from an ordered list of scenes. Each scene is
either a title card or a live RHOAI portal tab capture, framed 1080p with
a course banner, a step/tab chip, and a wrapped caption band that mirrors
the voice-over. Narration is neural TTS (Kokoro); assembly is ffmpeg.

A tab scene can list cuts. The narration is spoken once, and the
screenshot changes when the voice reaches each cut's phrase.

Usage: python3 produce.py <course_spec.json>
The spec drives everything so all five courses share one pipeline.
"""
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
FONT = "/Users/mdeneve/Library/Fonts/DejaVuSans.ttf"
FONT_B = "/Users/mdeneve/Library/Fonts/DejaVuSans-Bold.ttf"
FONT_M = "/Users/mdeneve/Library/Fonts/DejaVuSansMono.ttf"
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
    d.text((120, H - 90), course.get("footer",
           "Agentic AI Stack · live on our DevOps cluster called Rome (RHOAI 3.5 EA · OpenShift 4.22 SNO)"),
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


def main(spec_path):
    spec = json.load(open(spec_path))
    course = spec["course"]
    scenes = spec["scenes"]
    BUILD.mkdir(parents=True, exist_ok=True)
    total = len(scenes)
    clips = []
    for i, sc in enumerate(scenes, 1):
        png = str(BUILD / ("f%02d.png" % i))
        wav = str(BUILD / ("a%02d.wav" % i))
        mp4 = str(BUILD / ("c%02d.mp4" % i))
        tab_total = sum(1 for x in scenes if x["type"] != "title")
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
        clips.append(mp4)
        print("scene %d/%d  %.1fs  %s" % (i, total, dur(wav),
              sc.get("tab", sc.get("title", ""))[:50]), flush=True)
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
    main(sys.argv[1])
