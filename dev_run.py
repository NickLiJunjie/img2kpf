from __future__ import annotations

from pathlib import Path

from img2kpf.app_core import AppRunConfig, execute_run
from img2kpf.gui.i18n import translate_gui_text
from img2kpf.i18n import resolve_language
from img2kpf.plugin_registry import DEFAULT_KFX_PLUGIN_ID

# =========================
# 程序员直跑入口（IDE 点运行）
# =========================
#
# 使用方式：
# 1) 修改下面 CONFIG 的参数
# 2) 直接运行本文件（右上角 Run）
#
# 输入模式会自动识别：
# - 单卷：input_dir 根目录直接是图片（jpg/jpeg/png）
# - 批量：input_dir 根目录下是多个子目录，每个子目录是一卷
#
# output_location 规则：
# - 单卷：填输出文件路径（.kpf 或 .kfx，取决于 output_format）
# - 批量：填输出目录路径


# ========= 参数选项说明 =========
# reading_direction:
# - "rtl" / "ltr"
#
# page_layout:
# - "facing" / "single"
#
# virtual_panels:
# - True / False
#
# panel_movement:
# - "vertical" / "horizontal"
#
# image_preset:
# - "none" / "kcc-current-like" / "kcc-legacy-like"
#
# crop_mode:
# - "off" / "smart" / "kcc-spread" / "kcc-spread-fill"
#
# target_size_text:
# - 空字符串 "" 表示不指定
# - 或者 "1240x1860" 这种格式
#
# preserve_color / autocontrast / autolevel:
# - "auto" / "enabled" / "disabled"
#
# gamma_auto:
# - True: 忽略 gamma_value，用 preset 默认值
# - False: 使用 gamma_value（> 0）
#
# jpeg_quality_auto:
# - True: 忽略 jpeg_quality_value，用 preset 默认值
# - False: 使用 jpeg_quality_value（1~100）
#
# output_format:
# - "kpf": 只输出 KPF
# - "kpf_kfx": 输出 KPF + KFX
# - "kfx_only": 最终只保留 KFX（中间 KPF 会删除）
# - "epub"/"mobi": 当前版本未接入（不要用）
#
# kfx_plugin:
# - 插件 ID / 插件目录 / 插件 zip 路径
# - 默认值可直接用 DEFAULT_KFX_PLUGIN_ID
#
# jobs:
# - 批量并行卷数（>=1），单卷会忽略
#
# language:
# - "zh" / "en" / 其他语言包代码（如 "fr"）
#
# verbose_log:
# - True / False

LANGUAGE = "zh"
VERBOSE_LOG = True
_LANG = resolve_language(LANGUAGE)

CONFIG = AppRunConfig(
    # 必填：输入目录（建议绝对路径）
    input_dir="/absolute/path/to/input_dir",
    # 建议填写：
    # - 单卷示例："/absolute/path/to/output/book.kpf"
    # - 批量示例："/absolute/path/to/output_dir"
    # 留空则自动推断
    output_location="",
    # 可选模板（留空用内置默认 profile）
    template_path="",
    # 单卷可选标题（留空用目录名）
    title="",
    # 仅 facing 可用；True=在最前补白页做跨页对齐
    shift=False,
    # 版式
    reading_direction="rtl",
    page_layout="facing",
    virtual_panels=True,
    panel_movement="vertical",
    # 图像
    image_preset="kcc-legacy-like",
    crop_mode="off",
    target_size_text="",  # 例如 "1240x1860"
    scribe_panel=True,
    preserve_color="auto",
    gamma_value=1.8,
    gamma_auto=True,
    autocontrast="auto",
    autolevel="auto",
    jpeg_quality_value=90,
    jpeg_quality_auto=True,
    # 输出
    emit_kfx=False,  # 兼容字段；真正以 output_format 为准
    output_format="kpf",  # "kpf" / "kpf_kfx" / "kfx_only"
    kfx_plugin=DEFAULT_KFX_PLUGIN_ID,
    jobs=1,
)


def _render(text: str) -> str:
    return translate_gui_text(text, _LANG)


def _format_failures(failures: tuple) -> str:
    lines: list[str] = []
    for item in failures:
        volume_name = Path(str(item.volume_dir)).name
        lines.append(f"- {volume_name}: {_render(item.reason)}")
    return "\n".join(lines)


def main() -> None:
    def _log(message: str) -> None:
        if VERBOSE_LOG:
            print(message)

    summary = execute_run(
        config=CONFIG,
        log_callback=lambda message: _log(_render(message)),
        status_callback=lambda message: _log(f"{_render('ui.status.title')}: {_render(message)}"),
    )
    mode_label = {"single": _render("ui.single"), "batch": _render("ui.batch")}.get(
        summary.mode, summary.mode
    )
    print(f"\n===== {_render('ui.status.run_complete')} =====")
    print(f"{_render('ui.mode')}: {mode_label}")
    print(f"{_render('ui.status.output_location')}: {summary.output_location}")
    print(f"{_render('ui.status.success')}: {len(summary.successes)}")
    print(f"{_render('ui.status.failed')}: {len(summary.failures)}")
    if summary.failures:
        print(f"{_render('ui.status.failure_details')}:")
        print(_format_failures(summary.failures))


if __name__ == "__main__":
    main()
