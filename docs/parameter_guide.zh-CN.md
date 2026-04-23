# img2kpf 参数手册

[English](parameter_guide.md) | [简体中文](parameter_guide.zh-CN.md)

这份手册重点解释三件事：

- 每个参数是做什么的
- 什么时候该用
- 它会怎样影响最终图片效果

建议和下面两个命令一起对照着看：

- `python kpf_generator.py --help`
- `python kfx_direct.py --help`

## 1）输入与输出

### `--input PATH`

- 作用：单卷模式
- 期望内容：一个目录里直接放图片
- 对图片的影响：没有；只是决定输入源

### `--batch PATH`

- 作用：批量模式
- 期望内容：每个子目录一卷
- 对图片的影响：没有；只是决定批量发现方式

### `--output PATH`

- 作用：单卷模式输出文件路径
- 对图片的影响：没有

### `--output-dir PATH`

- 作用：批量模式输出目录
- 对图片的影响：没有

### `--title TEXT`

- 作用：单卷模式可选标题元数据
- 对图片的影响：没有

### `--jobs INT`

- 作用：批量并行 worker 数
- 对图片的影响：没有直接影响
- 实际意义：卷数很多时可提速，但会提高 CPU / 磁盘压力

## 2）KFX 生成

### `--emit-kfx`

- 作用：在生成 `.kpf` 后继续产出 `.kfx`
- 前提：你需要自己准备 `KFX Output.zip`
- 对图片的影响：本身不改像素，只是多一个转换阶段

### `--kfx-plugin TEXT`

- 可传：
  - 插件 ID，例如 `kfx_output`
  - 插件目录
  - zip 绝对路径
- 对图片的影响：没有直接影响
- 实际意义：决定从哪里加载 KFX Output 插件源码

## 3）阅读布局

### `--reading-direction {rtl,ltr}`

- 控制阅读方向
- 推荐：
  - 日漫 / 中式右翻多用 `rtl`
  - 西漫或左到右阅读用 `ltr`
- 对图片的影响：
  - 会改变双页时“内侧 / 外侧”的判断
  - 会影响 spread 联动裁边的逻辑

### `--page-layout {facing,single}`

- `facing`：两页成组
- `single`：每张图单独一页
- 对图片的影响：
  - `facing` 才能启用双页联动裁边
  - `single` 不做双页配对，也不支持 shift

### `--shift`

- 在最前面补一张空白页，让第一张真实页落到第二槽位
- 适用：开篇跨页对齐不对的时候
- 对图片的影响：
  - 不会改原图像素
  - 但会改变 spread 配对方式，因此会间接影响双页裁边结果

### `--virtual-panels / --no-virtual-panels`

- 控制 Kindle Virtual Panels 开关
- 对图片的影响：
  - 不改页面像素
  - 影响 Kindle 端的阅读行为

### `--panel-movement {vertical,horizontal}`

- 只有开启 Virtual Panels 时才有意义
- 对图片的影响：
  - 不改像素
  - 影响面板导航方向元数据

## 4）图像预设

### `--image-preset {none,standard,bright}`

这个参数是影响“画面观感”的主开关。

#### `none`

- Gamma：`1.0`
- 自动对比度：关闭
- Autolevel：关闭
- 保留彩色：开启
- JPEG 质量：`90`
- 对图片的影响：
  - 最接近原图
  - 适合你已经在外部做过处理的图片

#### `standard`

- Gamma：`1.0`
- 自动对比度：开启
- Autolevel：关闭
- 保留彩色：开启
- JPEG 质量：`90`
- 对图片的影响：
  - 会把层次稍微拉开
  - 对偏灰、偏平的扫描图常常更清晰

#### `bright`

- Gamma：`1.8`
- 自动对比度：开启
- Autolevel：关闭
- 保留彩色：开启
- JPEG 质量：`90`
- 对图片的影响：
  - 比前两个预设更亮
  - 常适合在 Kindle Scribe 上看偏暗的漫画扫描
  - 但如果原图已经很亮，可能会让高光发白

## 5）裁边行为

### `--crop-mode off`

- 完全不裁边
- 对图片的影响：
  - 保留完整页面
  - 原始白边 / 黑边都会保留
  - 是最稳妥的起点

### `--crop-mode smart`

- 单页保守裁边
- 对图片的影响：
  - 裁掉明显的外边框
  - 会保留安全余量
  - 当内容离边缘太近时，不会特别激进

### `--crop-mode spread-safe`

- 双页联动裁边
- 对图片的影响：
  - 尽量让左右页看起来一致
  - 可以减少同一 spread 左右页裁边不对称
  - 比 fill 更稳妥

### `--crop-mode spread-fill`

- 双页联动裁边 + 在安全前提下减少中缝留白
- 对图片的影响：
  - 目标是减少中缝空白，让页面更铺满高度
  - 比 `spread-safe` 更激进
  - 在 Scribe 上往往更有“铺满屏幕”的感觉
  - 但书与书差异较大，建议逐本测试

## 6）画布与设备适配

### `--target-size WIDTHxHEIGHT`

- 把处理后的页面放进固定尺寸画布里，不拉伸
- 例如：`1240x1860`
- 对图片的影响：
  - 保持纵横比
  - 不够的地方会补白边
  - 让不同尺寸来源的书在输出上更统一

### `--scribe-panel`

- `1240x1860` 的快捷开关
- 对图片的影响：
  - 等价于 `--target-size 1240x1860`
  - 适合 Kindle Scribe 横屏双页单槽位

## 7）亮度与色彩控制

### `--preserve-color / --no-preserve-color`

- 控制是否保留彩色处理链
- 对图片的影响：
  - `--preserve-color`：彩页保留颜色
  - `--no-preserve-color`：更适合纯黑白漫画，输出更接近灰度流程

### `--gamma FLOAT`

- 控制亮度 gamma
- 常见值：
  - `1.0`：不提亮
  - `1.8`：较亮的旧版风格
- 对图片的影响：
  - 值更高时，中间调会更亮
  - 太高会让对比变平、亮部发灰发白

### `--autocontrast / --no-autocontrast`

- 自动拉伸对比范围
- 对图片的影响：
  - 线稿可能更清楚
  - 噪点多的扫描也可能被放大

### `--autolevel / --no-autolevel`

- 轻量黑位增强
- 对图片的影响：
  - 能让偏灰的黑色稍微更实
  - 通常是比较轻的调整

### `--jpeg-quality INT`

- 最终 JPEG 质量
- 常用值：`90`
- 对图片的影响：
  - 值越高，细节保留越好、压缩痕迹越少
  - 值越低，文件更小，但文字边缘和细线更容易出现压缩噪点

## 8）可选模板

### `--template PATH`

- 作用：兼容 / 实验入口
- 接受 Kindle Create 导出的 `.kpf` / `.zip`
- 对图片的影响：
  - 通常不直接改像素
  - 但可能影响布局元数据和兼容性表现

## 9）推荐起步方案

### 稳妥基线

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

### 偏亮、偏 Scribe 的方案

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

### 更激进的铺满方案

```bash
python kpf_generator.py \
  --input ./manga/Volume_01 \
  --output ./output/Volume_01.kpf \
  --image-preset bright \
  --crop-mode spread-fill \
  --scribe-panel
```

## 10）实战建议

- 先从 `--crop-mode off` 起步，再决定是否真的需要裁边。
- 如果双页看起来左右不一致，先试 `spread-safe`，再试 `spread-fill`。
- 如果真机上偏暗，优先试 `bright` 或手动提高 `--gamma`。
- 如果彩页发灰，就保留 `--preserve-color`。
- 如果输入图已经是你精修过的，优先从 `--image-preset none` 开始。
