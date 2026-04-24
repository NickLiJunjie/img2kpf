[English](README.md) | [简体中文](README.zh-CN.md)

# img2kpf

`img2kpf` converts folders of comic images into Kindle Create-compatible `.kpf` packages, and can optionally continue to `.kfx` by loading a **user-supplied** `KFX Output.zip`.

This public version intentionally ships only project code and first-party project assets:

- no bundled manga pages
- no bundled `KFX Output.zip`
- no local research notes or sample books

## Features

- Build a single `.kpf` from one image folder
- Build multiple volumes in batch mode
- Optional direct `.kpf -> .kfx` conversion through a local `KFX Output.zip`
- Built-in GUI with English / Chinese language switching
- Layout controls for RTL / LTR, facing / single, Virtual Panels, and first-page shift
- Image processing controls for crop mode, gamma, autocontrast, grayscale/color, JPEG quality, and fixed target size

## Language

- This README is the default English homepage.
- Chinese guide: `README.zh-CN.md`
- English parameter guide: `docs/parameter_guide.md`
- Chinese parameter guide: `docs/parameter_guide.zh-CN.md`

## Requirements

### Runtime

- Python `3.10+`
- one of:
  - `uv` (recommended), or
  - `pip` inside a virtual environment

### Python packages

Core:

- `Pillow`
- `lxml`
- `beautifulsoup4`

Optional GUI:

- `PySide6`

Install lists:

- `requirements.txt`
- `requirements-gui.txt`

## Installation

### Option A: `uv`

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

On Windows, activate `.venv\\Scripts\\activate` instead.

For the GUI:

```bash
uv pip install -r requirements-gui.txt
```

### Option B: standard `venv` + `pip`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate `.venv\\Scripts\\activate` instead of `source .venv/bin/activate`.

For the GUI:

```bash
pip install -r requirements-gui.txt
```

## KFX Output download

`img2kpf` does **not** bundle the KFX Output plugin archive. Download it yourself from the upstream MobileRead thread:

- MobileRead thread: [KFX Output plugin](https://www.mobileread.com/forums/showthread.php?t=272407)

### Where to put the zip

You have two choices:

1. Put the downloaded file at:

   - `img2kpf/plugins/kfx_output/KFX Output.zip`

2. Keep it anywhere and pass the path explicitly:

   - `--kfx-plugin "/path/to/KFX Output.zip"`
   - `python kfx_direct.py --plugin "/path/to/KFX Output.zip" ...`

If you prefer the calibre UI path, you can also install the downloaded zip through calibre's plugin manager (`Preferences -> Plugins -> Load plugin from file`).

## Quick start

### 1) Generate KPF only

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --scribe-panel
```

### 2) Generate KPF and KFX

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --scribe-panel \
  --emit-kfx \
  --kfx-plugin "./img2kpf/plugins/kfx_output/KFX Output.zip"
```

### 3) Batch mode

```bash
python kpf_generator.py \
  --batch ./manga \
  --output-dir ./output \
  --scribe-panel \
  --jobs 4
```

### 4) Convert an existing KPF into KFX

```bash
python kfx_direct.py \
  --input ./output/Volume_01.kpf \
  --plugin "./img2kpf/plugins/kfx_output/KFX Output.zip"
```

## GUI

Launch the GUI:

```bash
python gui_app.py
```

GUI notes:

- English / Chinese UI switching is built in.
- The GUI covers the same conversion options as the CLI.
- Preview uses the same processing pipeline as export, so it is a high-confidence preview rather than a separate mockup.

## Image processing defaults

Current commonly used defaults:

- `--reading-direction rtl`
- `--page-layout facing`
- Virtual Panels enabled
- `--panel-movement vertical`
- `--crop-mode off`
- `--image-preset bright`
- `--scribe-panel`

Actual preset defaults:

| Preset | Gamma | Autocontrast | Autolevel | Preserve color | JPEG quality |
| --- | ---: | --- | --- | --- | ---: |
| `none` | `1.0` | off | off | on | `90` |
| `standard` | `1.0` | on | off | on | `90` |
| `bright` | `1.8` | on | off | on | `90` |

## Parameter guide

The detailed parameter guide explains **what each option does** and **how it changes the image result**:

- English: `docs/parameter_guide.md`
- Chinese: `docs/parameter_guide.zh-CN.md`

Highlights:

- `--crop-mode off` keeps the full page and preserves margins
- `--crop-mode smart` trims obvious outer borders conservatively
- `--crop-mode spread-safe` keeps facing pages visually synchronized
- `--crop-mode spread-fill` is more aggressive and tries to reduce inner white margins when safe
- `--gamma` changes luminance
- `--preserve-color` keeps color instead of converting toward grayscale output
- `--jpeg-quality` trades file size against compression artifacts

## Optional template input

You can still pass an external Kindle Create-exported `.kpf` / `.zip` template through `--template` for compatibility experiments, but normal usage does not require bundling third-party sample books in the repo.

## Repository layout

- `img2kpf/` — core package
- `img2kpf/gui/` — GUI implementation
- `img2kpf/tools/kpf_analyzer.py` — KPF structure inspection helper
- `img2kpf/plugins/` — plugin manifests and user-provided plugin slots
- `docs/parameter_guide.md` — English parameter guide
- `docs/parameter_guide.zh-CN.md` — Chinese parameter guide
- `THIRD_PARTY_NOTICES.md` — third-party notices and download links

## License

- Project license: `MIT`
- See `LICENSE`
- Optional third-party tools keep their own upstream licenses

## Suggestions

If you plan to publish this repository broadly, these are still good next steps:

- add CI for `python -m compileall` and basic smoke tests
- consider an explicit `SECURITY.md`
- if you want a stricter clean-room distribution later, make `--template` mandatory instead of relying on the built-in profile asset
