# Agent.md — img2kpf 仓库协作说明

## 项目当前目标

这个仓库的当前目标很明确：

- 输入：漫画原图目录（`jpg` / `jpeg` / `png`）
- 输出：可被 calibre `KFX Output` 消费的 `.kpf`
- 后续流程：`.kpf -> .kfx -> Kindle`

当前已经不是“从零猜 KPF 格式”的阶段，而是：

- 用脚本稳定生成可用的漫画 `.kpf`
- 维护默认内置 profile
- 继续做必要的兼容性与画质调优

## 当前实现状态

- 主脚本：`kpf_generator.py`
- 默认内置静态资产：`img2kpf/assets/kc_comics_rtl_facing.json`
- 默认运行**不再依赖外部模板**
- `--template` 现在只是实验/兼容用的可选覆盖入口，用于逆向新 profile

## 当前默认 profile

默认 profile 面向：

- RTL 漫画
- 双页 spread
- Kindle Scribe 横屏双页阅读

默认图像参数结论：

- `--crop-mode off`
- `--image-preset kcc-legacy-like`
- 常与 `--scribe-panel` 搭配使用

如果用户没有特别指定，默认就按这组执行。

## 当前 CLI 真实能力

以 `kpf_generator.py --help` 为准。当前已稳定支持：

- `--input`
- `--batch`
- `--output`
- `--output-dir`
- `--title`
- `--template`（可选覆盖）
- 版式相关参数：
  - `--reading-direction`
  - `--page-layout`
  - `--virtual-panels`
  - `--panel-movement`
- 图像预处理相关参数

当前**还没有**开放完整元数据参数，例如：

- `--author`
- `--publisher`

不要在文档里再写这些尚未实现的参数。

## 文档维护原则

准备上传 GitHub 时，优先保证：

- `README.md` 只写当前真实可用的用法
- 不保留大量本地试验叙事
- 不把用户本机绝对路径当成默认示例
- 如果有历史样本或研究记录，明确标成“实验记录”而不是“必需文件”

## calibre / KFX Output 实战注意事项

这个项目在验证 `.kpf -> .kfx` 时，经常会遇到 `calibre` 和 `KFX Output` 插件相关问题。默认按下面规则处理。

### 常见现象

- `calibre-debug -r "KFX Output"` 提示找不到插件
- `calibre-customize -a ...` 安装插件时报权限错误
- 访问 `.analysis\calibre_config_*` 下的插件目录时被拒绝

### 根因优先级

优先怀疑：

1. `CALIBRE_CONFIG_DIRECTORY`
2. 插件安装位置
3. 权限 / ACL

不要第一时间怀疑 KPF 本身坏了。

### 推荐做法

默认使用项目内隔离 calibre 配置：

- `D:\code\img2kpf\.analysis\calibre_config_3`

验证前先设置：

```powershell
$env:CALIBRE_CONFIG_DIRECTORY='D:\code\img2kpf\.analysis\calibre_config_3'
```

若插件未安装：

```powershell
& 'C:\Program Files\Calibre2\calibre-customize.exe' -a 'D:\code\img2kpf\.analysis\KFX Output.zip'
```

转换命令：

```powershell
& 'C:\Program Files\Calibre2\calibre-debug.exe' -r 'KFX Output' -- input.kpf output.kfx
```

## `.analysis` 目录约定

`.analysis/` 只放：

- 临时分析产物
- calibre 隔离配置
- 本地插件包

这些内容默认不应进入 Git 仓库。

如果 `.analysis/` 积累了大量测试产物，清理时优先保留：

- `.analysis\KFX Output.zip`
- `.analysis\calibre_config_3`

## 重要提醒

- 当前仓库已经可以无模板批量生成 `.kpf`
- 当前仓库已经验证过批量 `.kpf -> .kfx`
- 如果未来新增 profile，优先新增新的 `assets/*.json`，而不是重新把运行时绑回外部模板
