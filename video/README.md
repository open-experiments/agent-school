# Course walkthrough videos

`produce.py` builds one narrated 1080p MP4 from a course spec. Each scene is a title card or a portal screenshot with a caption. Speech is [Kokoro](https://github.com/hexgrad/kokoro) (`af_sarah` by default). Frames and the final concat are ffmpeg.

Run it from the **repository root**. Spec image paths and the `output` path are relative to that directory, not to `video/`.

## Prerequisites

- Python **3.10, 3.11, or 3.12**. Kokoro 0.9.4 refuses 3.13 and newer.
- **ffmpeg** on `PATH`, including `ffprobe`, libx264, and AAC.
- **espeak-ng**, used by Kokoro for English words outside its dictionary.
- DejaVu Sans and DejaVu Sans Bold. The script opens these paths:

  - `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`
  - `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`

- Network on the first synthesis. Kokoro downloads the Kokoro-82M weights and the selected voice from Hugging Face into the local Hugging Face cache.

### Linux

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core
```

`fonts-dejavu-core` installs the faces at the paths above.

### macOS

```bash
brew install ffmpeg espeak-ng python@3.12
brew install --cask font-dejavu
```

Homebrew puts DejaVu in `~/Library/Fonts`, and macOS will not let you create `/usr/share/fonts`. Before the first run, point the `FONT` and `FONT_B` constants at the top of `produce.py` at the installed files, typically:

- `~/Library/Fonts/DejaVuSans.ttf`
- `~/Library/Fonts/DejaVuSans-Bold.ttf`

`FONT_M` is unused. `/usr/bin/python3` on macOS is 3.9, and Kokoro 0.9.4 will not install there. Create the virtualenv with Homebrew's Python 3.12, not `python3`.

## Install

From the repository root. If `video/.venv` already exists and was built with the wrong Python, delete it first (`rm -rf video/.venv`); a virtualenv keeps the interpreter it was created with.

Linux, when `python3 --version` is 3.10, 3.11, or 3.12:

```bash
python3 -m venv video/.venv
source video/.venv/bin/activate
python -m pip install -U pip
pip install -r video/requirements.txt
```

macOS:

```bash
brew install python@3.12
rm -rf video/.venv
"$(brew --prefix python@3.12)/bin/python3.12" -m venv video/.venv
source video/.venv/bin/activate
python -m pip install -U pip
pip install -r video/requirements.txt
```

`python -c 'import sys; print(sys.version)'` inside the virtualenv should report 3.12. The first `pip install` is large because Kokoro depends on PyTorch. A CPU wheel is enough.

`video/.venv/` is gitignored.

## Produce a course

Still at the repository root, with the virtualenv active:

```bash
python video/produce.py video/specs/101.json
```

The five specs:

| Spec | MP4 written (overwrites the file if it exists) |
|------|------------------------------------------------|
| `video/specs/101.json` | `101-noc-assistant/images/101-noc-assistant.mp4` |
| `video/specs/201.json` | `201-rca-investigator/images/201-rca-investigator.mp4` |
| `video/specs/202.json` | `202-fraud-triage/images/202-fraud-triage.mp4` |
| `video/specs/301.json` | `301-closed-loop-netops/images/301-closed-loop-netops.mp4` |
| `video/specs/302.json` | `302-energy-optimizer/images/302-energy-optimizer.mp4` |

The script checks the spec before it synthesizes anything. The file has to be valid JSON, each scene needs the fields that scene type uses, every `cuts` phrase has to occur in that scene's `say`, and every screenshot path has to be a readable image. Paths are relative to the current directory, so run it from the repository root. A problem prints every finding and exits without writing a video. When the spec is fine it prints `spec ok` and then builds.

Each scene is cached under `$BUILD_DIR/cache`. A later run re-speaks only the scenes whose narration, screenshots, caption, or step label changed, then stitches every scene into the final mp4. The log marks a reused scene with `cached`. Replacing a PNG at the same path counts as a change. Adding or removing a scene changes the `n / total` on the title cards and the step chip on every tab, so those cards are rendered again. `PRODUCE_REBUILD=1` ignores the cache. Deleting the cache directory does the same.

Each scene prints its index, narration length, and title. The last line is `DONE` plus the output path and total duration. Intermediate PNGs, WAVs, and scene clips go to `/tmp/vidbuild` and can be deleted after a successful run.

### Several screenshots in one scene

Each scene holds its picture for 0.6 seconds after its narration ends. Splitting one sentence into several scenes puts that pause between the pieces, and the voice starts over at each piece.

Keep it as one scene and add `cuts`. `image` is the screenshot at the start. Each cut swaps the screenshot when the voice reaches `at`, a phrase copied from `say`. The narration is synthesized once, so the picture changes without a gap in the voice. The caption on the right stays the scene caption. The 0.6 second hold happens once, after the whole narration.

```json
"image": "101-noc-assistant/images/rhoai/model-traces.png",
"say": "And traced reasoning. Every run lands here as a full trace: the question it was asked, every tool call it made, the tokens it spent, and how long each step took.",
"cuts": [
  {"at": "the question it was asked", "image": "101-noc-assistant/images/rhoai/model-traces-highlight-request.png"},
  {"at": "the tokens it spent", "image": "101-noc-assistant/images/rhoai/model-traces-highlight-tokens.png"}
]
```

`at` is matched against the words that are spoken. Leave out `[word](/phonemes/)` markup. While `produce.py` runs, each cut prints the time it takes effect.

### Environment

| Variable | Default | Effect |
|----------|---------|--------|
| `KOKORO_VOICE` | `af_sarah` | Kokoro voice id. It must be an American English voice (`af_*` or `am_*`) because the pipeline is fixed to `lang_code="a"`. |
| `BUILD_DIR` | `/tmp/vidbuild` | Directory for per-scene frames, audio, clips, and the scene cache. |
| `PRODUCE_REBUILD` | unset | Set to `1` to re-speak every scene instead of reusing cached clips. |

Example:

```bash
KOKORO_VOICE=af_heart BUILD_DIR=/tmp/vidbuild-101 \
  python video/produce.py video/specs/101.json
```

## After the MP4

`produce.py` leaves the durations on the site, in the course READMEs, and in `docs/tests/site.test.js` as they are. From the repository root, with ffmpeg still on `PATH` (no extra Python packages):

```bash
python3 video/sync-durations.py          # rewrite those durations from the five MP4s
python3 video/sync-durations.py --check  # exit 1 if any of them drifted
```

`sync-durations.py` reads all five course MP4s, so every course file has to be present before `--check` or a full sync.
