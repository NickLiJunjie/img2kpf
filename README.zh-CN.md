[English](README.md) | [简体中文](README.zh-CN.md)

# img2kpf

`img2kpf` 用于把漫画图片目录打包成 Kindle Create 兼容的 `.kpf`，并且可以在你**自行提供** `KFX Output.zip` 的前提下继续产出 `.kfx`。

当前这个公开版仓库刻意只保留项目代码和一方项目资源：

- 不附带漫画示例图片
- 不附带 `KFX Output.zip`
- 不附带本地研究记录和样本书

## 功能概览

- 单卷图片目录生成 `.kpf`
- 批量模式一次处理多卷
- 可选直接 `.kpf -> .kfx`，但需要你自己下载 `KFX Output.zip`
- 自带 GUI，支持英文 / 中文切换
- 支持 RTL / LTR、双页 / 单页、Virtual Panels、首页 shift
- 支持裁边、gamma、自动对比度、灰度 / 彩色、JPEG 质量、固定目标尺寸等图像处理参数

## 语言与文档

- 英文主页：`README.md`
- 中文主页：`README.zh-CN.md`
- 英文参数手册：`docs/parameter_guide.md`
- 中文参数手册：`docs/parameter_guide.zh-CN.md`

## 依赖要求

### 运行环境

- Python `3.10+`
- 推荐：
  - `uv`
  - 或虚拟环境里的 `pip`

### Python 包

核心依赖：

- `Pillow`
- `lxml`
- `beautifulsoup4`

可选 GUI：

- `PySide6`

依赖清单文件：

- `requirements.txt`
- `requirements-gui.txt`

## 安装

### 方案 A：使用 `uv`

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Windows 下把激活命令改成 `.venv\\Scripts\\activate`。

如果你要启动 GUI：

```bash
uv pip install -r requirements-gui.txt
```

### 方案 B：使用 `venv` + `pip`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows 下同样使用 `.venv\\Scripts\\activate`，而不是 `source .venv/bin/activate`。

如果你要启动 GUI：

```bash
pip install -r requirements-gui.txt
```

## KFX Output 到哪里下载

本仓库**不附带** `KFX Output.zip`。请前往上游 MobileRead 主题帖自行下载：

- MobileRead 主题帖：[KFX Output plugin](https://www.mobileread.com/forums/showthread.php?t=272407)

### 下载后放哪里

你有两种方式：

1. 放到仓库默认位置：

   - `img2kpf/plugins/kfx_output/KFX Output.zip`

2. 放在任意位置，然后显式传路径：

   - `--kfx-plugin "/path/to/KFX Output.zip"`
   - `python kfx_direct.py --plugin "/path/to/KFX Output.zip" ...`

如果你更习惯 calibre 图形界面，也可以在 calibre 里通过 `Preferences -> Plugins -> Load plugin from file` 安装你下载好的 zip。

## 快速使用

### 1）只生成 KPF

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --scribe-panel
```

### 2）生成 KPF + KFX

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --scribe-panel \
  --emit-kfx \
  --kfx-plugin "./img2kpf/plugins/kfx_output/KFX Output.zip"
```

### 3）批量模式

```bash
python kpf_generator.py \
  --batch ./manga \
  --output-dir ./output \
  --scribe-panel \
  --jobs 4
```

### 4）把已有 KPF 转成 KFX

```bash
python kfx_direct.py \
  --input ./output/Volume_01.kpf \
  --plugin "./img2kpf/plugins/kfx_output/KFX Output.zip"
```

## GUI

启动 GUI：

```bash
python gui_app.py
```

说明：

- GUI 内建中英文切换
- GUI 参数覆盖同一套核心转换链路
- 预览与实际导出共用图像处理逻辑，因此是高一致性预览

## 当前常用默认组合

- `--reading-direction rtl`
- `--page-layout facing`
- 开启 Virtual Panels
- `--panel-movement vertical`
- `--crop-mode off`
- `--image-preset bright`
- `--scribe-panel`

预设实际默认值如下：

| 预设 | Gamma | 自动对比度 | Autolevel | 保留彩色 | JPEG 质量 |
| --- | ---: | --- | --- | --- | ---: |
| `none` | `1.0` | 关闭 | 关闭 | 开启 | `90` |
| `standard` | `1.0` | 开启 | 关闭 | 开启 | `90` |
| `bright` | `1.8` | 开启 | 关闭 | 开启 | `90` |

## 参数手册

更详细的参数说明与“它会怎样影响图片结果”，见：

- 英文：`docs/parameter_guide.md`
- 中文：`docs/parameter_guide.zh-CN.md`

快速理解：

- `--crop-mode off`：不裁边，完整保留原页边距
- `--crop-mode smart`：保守裁掉明显外边框
- `--crop-mode spread-safe`：双页联动，尽量保持左右页一致
- `--crop-mode spread-fill`：更激进，会在安全前提下尽量减少中缝白边
- `--gamma`：改变亮度
- `--preserve-color`：保留彩色，不往灰度方向收敛
- `--jpeg-quality`：在文件体积与压缩痕迹之间取舍

## 可选模板

你仍然可以通过 `--template` 传入 Kindle Create 导出的 `.kpf` / `.zip` 做兼容性验证；但公开仓库本身不再附带第三方样本书或插件压缩包。

## 仓库结构

- `img2kpf/`：核心包
- `img2kpf/gui/`：GUI 实现
- `img2kpf/tools/kpf_analyzer.py`：KPF 结构分析工具
- `img2kpf/plugins/`：插件清单与用户自备插件位置
- `docs/parameter_guide.md`：英文参数手册
- `docs/parameter_guide.zh-CN.md`：中文参数手册
- `THIRD_PARTY_NOTICES.md`：第三方依赖与下载说明

## 许可证

- 项目许可证：`MIT`
- 见 `LICENSE`
- 可选第三方工具仍然遵循它们各自的上游许可证

## 进一步建议

如果你准备长期公开维护，建议继续补：

- 基础 CI，例如 `python -m compileall`
- 一个简单 smoke test
- `SECURITY.md`
- 如果你想做更严格的 clean-room 版本，可以把 `--template` 变成强制项，彻底不依赖内置 profile 资产
