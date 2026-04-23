# img2kpf Parameter Guide

[English](parameter_guide.md) | [简体中文](parameter_guide.zh-CN.md)

This guide explains:

- what each option does
- when to use it
- how it changes the exported image result

Use it together with:

- `python kpf_generator.py --help`
- `python kfx_direct.py --help`

## 1. Input and output

### `--input PATH`

- Purpose: single-volume mode
- Expected content: image files directly inside one folder
- Image impact: none; this only selects the source set

### `--batch PATH`

- Purpose: batch mode
- Expected content: one subfolder per volume
- Image impact: none; this only changes how multiple folders are discovered

### `--output PATH`

- Purpose: output file path in single-volume mode
- Image impact: none

### `--output-dir PATH`

- Purpose: output directory in batch mode
- Image impact: none

### `--title TEXT`

- Purpose: optional title metadata for single-volume mode
- Image impact: none

### `--jobs INT`

- Purpose: number of parallel workers in batch mode
- Image impact: none directly
- Practical impact: higher values can speed up large batches but increase CPU / disk pressure

## 2. KFX generation

### `--emit-kfx`

- Purpose: continue from generated `.kpf` to `.kfx`
- Requirement: a user-downloaded `KFX Output.zip`
- Image impact: none by itself; it only adds an extra conversion stage

### `--kfx-plugin TEXT`

- Accepted values:
  - plugin ID such as `kfx_output`
  - plugin directory
  - direct zip path
- Image impact: none directly
- Practical impact: determines where img2kpf loads the KFX Output code from

## 3. Reading layout

### `--reading-direction {rtl,ltr}`

- Controls page order assumptions
- Recommended:
  - `rtl` for most Japanese / Chinese manga layouts
  - `ltr` for western comics or left-to-right reading
- Image impact:
  - changes facing-page inner/outer edge interpretation
  - matters for linked spread crop logic

### `--page-layout {facing,single}`

- `facing`: two pages are grouped as spreads
- `single`: every image becomes an independent page
- Image impact:
  - `facing` enables spread-aware crop behavior
  - `single` disables spread pairing and shift mode

### `--shift`

- Prepends one blank page so the first real page lands in slot 2
- Use it when a book's opening alignment looks wrong in facing mode
- Image impact:
  - no pixel change to the original images
  - changes spread pairing and therefore can change which pages are linked together for crop decisions

### `--virtual-panels / --no-virtual-panels`

- Turns Kindle Virtual Panels on or off
- Image impact:
  - no raster change to page pixels
  - affects reading behavior on supported Kindle devices

### `--panel-movement {vertical,horizontal}`

- Only meaningful when Virtual Panels are enabled
- Image impact:
  - no raster change
  - affects panel navigation direction metadata

## 4. Image presets

### `--image-preset {none,standard,bright}`

This option is the main shortcut for image look.

#### `none`

- Gamma: `1.0`
- Autocontrast: off
- Autolevel: off
- Preserve color: on
- JPEG quality: `90`
- Image impact:
  - closest to the source
  - useful when you already prepared images externally

#### `standard`

- Gamma: `1.0`
- Autocontrast: on
- Autolevel: off
- Preserve color: on
- JPEG quality: `90`
- Image impact:
  - slightly stronger tonal separation
  - improves flat-looking grayscale scans without brightening the whole page too much

#### `bright`

- Gamma: `1.8`
- Autocontrast: on
- Autolevel: off
- Preserve color: on
- JPEG quality: `90`
- Image impact:
  - brighter output than the other presets
  - often useful for darker manga scans on Kindle Scribe
  - can wash out highlights if the source is already bright

## 5. Crop behavior

### `--crop-mode off`

- No crop
- Image impact:
  - preserves the full page
  - keeps original white / black margins
  - safest option when pages are already clean

### `--crop-mode smart`

- Conservative single-page crop
- Image impact:
  - trims obvious outer borders
  - keeps a safety margin
  - avoids aggressive cropping when content is too close to the edge

### `--crop-mode spread-safe`

- Linked crop for facing pages
- Image impact:
  - tries to keep left/right pages visually synchronized
  - reduces asymmetry across a spread
  - safer than fill mode for books with tight gutters or uneven scans

### `--crop-mode spread-fill`

- Linked spread crop plus safe inner-margin reduction
- Image impact:
  - aims to reduce empty gutter space and fill height better
  - more aggressive than `spread-safe`
  - can improve full-screen presence on Scribe
  - should be tested book by book because gutter-sensitive art may prefer `spread-safe`

## 6. Canvas and device fit

### `--target-size WIDTHxHEIGHT`

- Fits each processed page onto a fixed canvas without stretching
- Example: `1240x1860`
- Image impact:
  - keeps aspect ratio
  - adds white padding when needed
  - makes output more predictable across books with mixed sizes

### `--scribe-panel`

- Shortcut for `1240x1860`
- Image impact:
  - same as `--target-size 1240x1860`
  - useful for Kindle Scribe landscape spread workflows

## 7. Tone and color controls

### `--preserve-color / --no-preserve-color`

- Controls whether the output stays in color-capable processing or shifts toward grayscale output
- Image impact:
  - `--preserve-color` keeps color pages intact
  - `--no-preserve-color` is useful for monochrome manga when you want simpler grayscale output

### `--gamma FLOAT`

- Controls luminance adjustment
- Common values:
  - `1.0`: no gamma brightening
  - `1.8`: brighter legacy-like look
- Image impact:
  - higher values brighten darker midtones
  - too much can flatten contrast and wash highlights

### `--autocontrast / --no-autocontrast`

- Expands tonal range automatically
- Image impact:
  - can make line art clearer
  - can also exaggerate noisy scans

### `--autolevel / --no-autolevel`

- Applies a light black-level lift
- Image impact:
  - can help weak blacks look denser
  - usually a subtle adjustment

### `--jpeg-quality INT`

- Output JPEG quality after processing
- Typical value: `90`
- Image impact:
  - higher values preserve detail and reduce compression artifacts
  - lower values reduce file size but can create ringing / blocking around text and lines

## 8. Optional template

### `--template PATH`

- Optional compatibility / experimentation input
- Accepts a Kindle Create-exported `.kpf` / `.zip`
- Image impact:
  - usually none on the raw pixels
  - can affect layout metadata and compatibility behavior

## 9. Recommended starting profiles

### Safe baseline

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --reading-direction rtl \
  --page-layout facing \
  --virtual-panels \
  --panel-movement vertical \
  --image-preset standard \
  --crop-mode off \
  --scribe-panel
```

### Brighter Scribe-oriented profile

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --reading-direction rtl \
  --page-layout facing \
  --virtual-panels \
  --panel-movement vertical \
  --image-preset bright \
  --crop-mode spread-safe \
  --scribe-panel
```

### More aggressive fill profile

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --image-preset bright \
  --crop-mode spread-fill \
  --scribe-panel
```

## 10. Practical advice

- Start with `--crop-mode off` before chasing whitespace issues.
- If spreads feel uneven, test `spread-safe` before `spread-fill`.
- If pages are too dark on device, try `bright` or raise `--gamma`.
- If color pages look dull, keep `--preserve-color`.
- If your inputs are already clean and preprocessed, start from `--image-preset none`.
