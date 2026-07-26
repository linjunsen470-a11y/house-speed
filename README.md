# 房产带看视频 · 自动变速剪辑

对竖屏/横屏**带看 walkthrough** 做智能变速：**不删除任何画面**，房间、客厅等有意义实景保持原速，转角、空墙、门板、急走等过渡段自动加速（2×～3.5× 可配）。

> 全程本地 CV 规则 + ffmpeg，**不需要大模型**。装好 Python 依赖与 ffmpeg 后一条命令即可。

本仓库只包含**生产代码与配置**；输入视频、成片与分析缓存请放在本地（已由 `.gitignore` 忽略）。

---

## 环境要求

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) / ffprobe 在 `PATH` 中
- 依赖：

```bash
pip install -r requirements.txt
```

`requirements.txt`：`opencv-python`、`numpy`、`PyYAML`、`Pillow`（花字 / 贴纸渲染）。

---

## 快速开始

```bash
# 使用默认 config.yaml（把路径换成你的素材）
python edit_speed.py path/to/input.mp4

# 指定配置与输出
python edit_speed.py path/to/input.mp4 -c config.yaml -o path/to/output.mp4

# 只看分段计划，跳过最终成片（调参时很有用）
python edit_speed.py path/to/input.mp4 --dry-run

# dry-run + review 代理：仍会编码低分辨率标注片，但不导出最终成片
python edit_speed.py path/to/input.mp4 --dry-run --review

# 首次准备素材（字体 / 贴纸 / BGM 曲库）
python scripts/bootstrap_assets.py
# 仅重新下载 BGM：
python scripts/fetch_bgm.py

# 变速 + 包装成片（全程花字 + 动态私信贴纸 + 仅 BGM）
python edit_speed.py path/to/input.mp4 --pack

# 只改花字/贴纸/BGM，复用已有 speed_raw
python edit_speed.py path/to/input.mp4 --pack-only

# 列出花字样式、贴纸 id、BGM 预设
python edit_speed.py --list-styles
```

成片默认写到：`<原文件名>_edited.mp4`（后缀可在配置里改）。

### 包装成片（`--pack`）

在变速之后叠加：

1. **花字**：默认 `douyin_estate` 三级（标题 / 卖点 / 价格），**全程显示**  
2. **CTA 贴纸**：默认 `dm_estate_cta` **暖橙花字一体式** + 星光点缀；**中段短窗 + 片尾再出**（可配置）  
3. **音频**：仅 BGM（内置免版税曲库或自备路径）  

每视频可写 **`<stem>.edit.yaml`**（示例见 `examples/sample.edit.yaml`），覆盖 `config.yaml` 的 `pack.*`。  
样式表、贴纸换色 id、预览板见 **`assets/README.md`**；BGM 署名见 **`assets/music/README.md`**。

> 仓库**不存放**任何输入/成片视频（`*.mp4` 等已 gitignore）。请用本地素材路径运行。

#### 每视频配置示例（`<stem>.edit.yaml`）

```yaml
pack:
  style: "douyin_estate"
  text:
    title: "绿湖全新未入住"
    highlights:
      - "101平三房"
      - "业主忍痛割爱"
    price: "单价5XXX"
  layout:
    y_rel: 0.255
    max_width_rel: 0.88
    highlights_mode: "join"   # 或 stack
  text_motion:
    enter: "fade"
    duration: 0.35
    bounce_px: 0              # 关闭文字跳动
    pulse: 0
    sparkle_anim: true        # 价格星光
  sticker:
    style: "dm_estate_cta"    # 锁定默认；换色见 assets/README.md
    text: "私信了解"          # 私 / 私信 / 私信我 / 私信了解
    enter: "slide_up"         # none | pop | slide_up
    enter_ms: 280
    start: 4.5
    duration: 2.5
    repeat_at_end: true
    end_lead: 2.8
    width_rel: 0.30
    x: 0.16
    y: 0.90
  audio:
    bgm: "pop_hook"           # curated 默认；或 sl01 / a3 / random
    volume: 0.80
```

| 想调什么 | 字段 |
|----------|------|
| 标题/卖点/价格文案 | `text.title` / `highlights` / `price` |
| 花字上下位置 | `layout.y_rel` |
| 卖点一行/分行 | `layout.highlights_mode`（`join` / `stack`） |
| 花字入场 / 星光 | `text_motion.enter` / `sparkle_anim` |
| CTA 样式（换色） | `sticker.style`（如 `dm_estate_cta_gold`） |
| CTA 文案（短） | `sticker.text` |
| CTA 位置 / 大小 | `sticker.x` `sticker.y` `sticker.width_rel` |
| CTA 时间窗 | `sticker.start` `duration` `repeat_at_end` `end_lead` |
| CTA 入场动效 | `sticker.enter` / `enter_ms`（默认 `slide_up`） |
| 背景音乐 | `audio.bgm` |

#### BGM（与 `assets/music/` 对齐）

| 组 | id 示例 |
|----|---------|
| **curated（默认）** | `pop_hook` / `pop_spark` / `pop_vibe` / `pop_soft` / `pop_drive` / `pop_clean` |
| shortlist | `sl01` … `sl12` |
| 本地 `a*.m4a` | `a1` … `a18`（缺号跳过） |
| 随机 | `random`（优先 curated） |

明细与可选 CC BY 下载：`assets/music/README.md`。

运行时会在本地生成中间产物（可删，可忽略提交）：

```
frames/<stem>-<sha1前8位>/
  motion.csv             # 逐帧运动/边缘指标
  analysis_cache.json    # 分析缓存指纹
  segments.json          # 最终分段与倍率
  filter_complex.txt     # ffmpeg 滤镜脚本
  summary.json           # 机器可读运行摘要
  review.mp4             # 可选：--review 代理
  speed_raw.mp4          # --pack 时的变速中间片（--pack-only 复用）
  pack_title.png|.apng   # 花字图层
  pack_filter_complex.txt
  pack_summary.json      # 包装参数与路径摘要
```

---

## 项目结构

```
clip/
├── edit_speed.py              # CLI 入口
├── evaluate_segments.py       # 分段结果 vs 手标评估
├── config.yaml                # 主配置
├── requirements.txt
├── README.md
├── assets/                    # 字体 / 贴纸 / 音乐说明（见 assets/README.md）
├── examples/sample.edit.yaml  # 每视频包装配置示例
├── scripts/
│   ├── bootstrap_assets.py    # 字体 + 贴纸 + BGM
│   ├── fetch_bgm.py           # 可选 CC BY 曲库下载
│   ├── audit_bgm.py           # 本地 BGM 听感/时长抽查（自用）
│   ├── select_rhythmic_bgm.py # 节奏短名单筛选（自用）
│   ├── rank_bgm_top.py        # BGM 排序辅助（自用）
│   └── export_shortlist.py    # 导出 shortlist/（自用）
├── tests/
└── walkthrough_edit/
    ├── config.py / analyze.py / classify.py
    ├── render.py / pipeline.py
    ├── pack.py                # 花字 + 贴纸 + BGM
    ├── text_styles.py         # 花字样式
    ├── stickers_gen.py        # 动态贴纸生成
    └── music_catalog.py       # BGM 预设与路径解析
```

### 流水线

```
输入视频
   │
   ▼
analyze  逐帧：亮度均值、对比度 std、Canny 边缘密度、帧差 motion
   │
   ▼
classify 平滑指标 → 规则打标 room / move / fast
   │
   ▼
segments 合并过短碎片 → overrides → 停留衰减 → 时间线校验
   │
   ▼
render   ffmpeg：trim + setpts(加速) + atempo + concat
   │
   ▼
pack（可选 --pack）  全程花字 + 动态私信贴纸 + 仅 BGM
   │
   ▼
*_edited.mp4
```

| 标签 | 默认速度 | 含义 |
|------|----------|------|
| `room` | 1.0×（可再衰减） | 结构清晰、运动不大的实景 |
| `move` | 2.2× | 过渡行走（运动偏大且结构偏弱） |
| `fast` | 3.5× | 转角 / 白墙 / 急转 |

### 方案 A：实景停留衰减（`pacing.room_hold_ramp`）

连续 `room` 段内按**已停留时长**自动提速，缓解「同一空间拍太久、信息重复」导致的拖沓；**仍不删除任何画面**。

默认曲线（相对该 room 段起点）：

| 停留 | 速度 |
|------|------|
| 0～4s | 1.0× |
| 4～7s | 1.5× |
| 7～10s | 2.0× |
| >10s | 2.8× |

在 `config.yaml` 的 `pacing` 中可改；`pacing.enabled: false` 可关闭。

### 阳台 / 外景保护（`scenic_edge_min`）

外景扫楼、树、天际线时**运动大但边缘很密**，旧逻辑会当成「急转」3.5×。  
现规则：边缘密度 ≥ `classify.scenic_edge_min`（默认 0.16）时，高速画面仍标为 `room`；  
停留衰减时若子段边缘 ≥ `pacing.scenic_skip_edge_min`（默认 0.18）则保持 1×，避免外景被越看越快。

| 调参 | 作用 |
|------|------|
| 外景仍被快进 | 略降 `scenic_edge_min`（如 0.14） |
| 室内急转被误保护 | 略升 `scenic_edge_min`（如 0.18） |
| 外景后半仍被衰减 | 略降 `scenic_skip_edge_min` |

仍不准时可用 `overrides` 强制某段 `kind: room`。

---

## `config.yaml` 调参指南

所有可调项都在根目录 **`config.yaml`**，修改后重新运行即可，无需改代码。

### 1. 速度（最常改）

```yaml
speeds:
  room: 1.0    # 实景；可设 0.9 略慢强调
  move: 2.2    # 走廊过渡
  fast: 3.5    # 转角；可试 3.0～5.0
```

- 成片仍偏长 → 提高 `fast` / `move`
- 转角太「闪」→ 降低 `fast`（如 `2.5`）

### 2. 分类阈值（决定谁被加速）

```yaml
classify:
  wall_edge_max: 0.035      # 边缘低于此 + 低对比 → 当空墙
  wall_std_max: 45.0
  very_fast_motion: 14.0    # 帧差高于此 → fast
  transitional_motion: 10.5
  transitional_edge_max: 0.055
```

| 现象 | 建议 |
|------|------|
| 房间内缓推被误加速 | 提高 `very_fast_motion`（如 16），或降低 `transitional_edge_max` |
| 明显转角仍原速 | 降低 `very_fast_motion`（如 12） |
| 白墙没被加速 | 略提高 `wall_edge_max`（如 0.045） |
| 纹理墙被当空墙 | 降低 `wall_edge_max` 或 `wall_std_max` |

### 3. 分析与平滑

```yaml
analysis:
  resize_width: 135
  resize_height: 240
  canny_low: 50
  canny_high: 150
  smooth_window: 7    # 奇数；越大越稳，短转角越容易被抹掉
```

### 4. 短段合并

```yaml
segments:
  min_duration: 0.40   # 短于此时长的孤岛并入邻居，减少闪切
```

分段过多（例如 >80）时 CLI 会打印 **warning**：导出可能变慢。可略提高 `min_duration`，或简化下面的 `pacing.room_hold_ramp`（停留衰减会把长 room 切成多段）。

### 5. 实景停留衰减（方案 A）

```yaml
pacing:
  enabled: true
  room_hold_ramp:
    - after: 0.0
      speed: 1.0
    - after: 4.0
      speed: 1.5
    - after: 7.0
      speed: 2.0
    - after: 10.0
      speed: 2.8
```

### 6. 人工覆盖（精确指定某段）

```yaml
overrides:
  - start: 10.0
    end: 12.5
    kind: room      # 强制原速
  - start: 20.0
    end: 22.0
    kind: fast      # 强制加速
```

先用 `--dry-run` 看 `segments.json` 时间轴，再写 overrides。  
也可为单条视频放 `<素材stem>.edit.yaml`（自动加载，勿提交敏感/临时覆盖）。

### 7. 编码与体积

变速必须**重编码**，不能直接 copy 原轨。  
若使用 `libx264` + 低 CRF，成片码率会远高于微信导出的 **HEVC 低码率** 原片，出现「剪短了反而更大」——这是编码设置问题，不是分段逻辑错误。

```yaml
encode:
  match_source: true    # 推荐：贴近原片码率；HEVC 原片 → libx265
  bitrate_scale: 1.0    # <1 更小；>1 更清晰
  video_codec: "auto"
  # match_source: false 时改用 CRF：
  # crf: 28
  # video_codec: "libx264"
```

### 8. 输入输出

```yaml
io:
  output_suffix: "_edited"
  work_dir: "frames"
  save_motion_csv: true
  save_segments_json: true
  save_filter_script: true
```

---

## 调参工作流（推荐）

1. `python edit_speed.py 素材.mp4 --dry-run` 看分段与预估时长  
2. 改 `config.yaml` 的 `speeds` / `classify` / `overrides`  
3. 再 dry-run，确认计划  
4. 去掉 `--dry-run` 导出成片并预览  
5. 对个别错误秒数加 `overrides` 精细修  

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `input` | 输入视频路径 |
| `-o` / `--output` | 输出路径 |
| `-c` / `--config` | 配置文件，默认 `config.yaml` |
| `--dry-run` | 只分析与打印计划，跳过最终成片导出（若同时加 `--review` 仍会编码 review 代理） |
| `--review [PATH]` | 额外导出低分辨率标注代理；即使有 `--dry-run` 也会编码 |
| `--reanalyze` | 忽略匹配的 motion 缓存，强制重新分析 |
| `--edit-config PATH` | 指定每视频 YAML 覆盖层 |
| `--json` | 仅向 stdout 打印机器可读 JSON 结果 |
| `--pack` | 变速后包装：全程花字 + 动态贴纸 + 仅 BGM |
| `--pack-only` | 跳过分析/变速，只对 `speed_raw.mp4` 重新包装 |
| `--list-styles` | 列出花字样式与贴纸 id |

---

## Agent / 自动化

分析步骤会缓存；`analysis` 参数与输入文件指纹一致时显示 `cache hit`。

```bash
python edit_speed.py path/to/input.mp4 --dry-run --reanalyze
python edit_speed.py path/to/input.mp4 --dry-run --review
python edit_speed.py path/to/input.mp4 --dry-run --json
```

- `--reanalyze`：忽略缓存重算  
- `--review [PATH]`：生成带标签的低分代理  
- `--json`：stdout 仅输出 JSON，便于 IDE Agent 解析  

每视频覆盖示例 `素材.edit.yaml`：

```yaml
overrides:
  - start: 10.0
    end: 12.5
    kind: room
```

工作目录：`frames/<stem>-<hash>/`，内含 `summary.json` 等运行摘要。

### 分段评估

手标 YAML 示例：

```yaml
labels:
  - start: 3.2
    end: 6.0
    kind: room
```

```bash
python evaluate_segments.py frames/<stem-hash>/segments.json labels.yaml
python -m unittest discover -v
```

---

## 设计约束

1. **不丢弃片段**：全程 `trim` + 变速 + `concat`，时间轴完整。  
2. **规则可解释**：分段结果可从 `motion.csv` + `segments.json` 复盘。  
3. **配置外置**：阈值与倍率集中在 YAML，便于 A/B 与复用。  
4. **本地可跑**：仅 OpenCV + numpy + ffmpeg。  
5. **仓库无素材**：视频与缓存不进版本库，避免体积膨胀。  

---

## 常见问题

**Q: 提示找不到 ffmpeg？**  
把 ffmpeg 加入系统 `PATH`，或在安装目录下确认 `ffmpeg -version` 可用。

**Q: 报错时只有一行 Error，不好查？**  
非 `--json` 模式下失败会把 **完整 traceback** 打到 stderr，便于对照代码行。

**Q: 导出到一半 Ctrl+C 了？**  
会尽量结束残留的 ffmpeg 进程，并删除未写完的 `.*.part.mp4` / pack 临时文件，避免留下半成品。

**Q: 提示 segment count is high？**  
见上文「短段合并」：提高 `segments.min_duration` 或简化 `pacing.room_hold_ramp`。功能仍正常，只是 ffmpeg 滤镜节点变多、可能更慢。

**Q: `--pack` 成片比只变速更糊/更小？**  
`--pack` 会在变速后再**重编码**一次（叠加花字/贴纸/BGM）。需要极致画质时可先出变速片，再在剪辑软件里叠字幕。

**Q: 音频变调/爆音？**  
`atempo` 会按相同倍率加速音频；极端倍率由多级 `atempo` 串联。无音轨视频会自动只处理画面。`--pack` 成片默认**去掉原声**，只留 BGM。

**Q: 能否批量处理？**  

```bash
python edit_speed.py a.mp4
python edit_speed.py b.mp4
# 或自行 for 循环
```

**Q: 和剪映手动比？**  
适合统一风格的带看粗剪 + 可选房产包装；仍可把 `*_edited.mp4` 再导入剪辑软件精修。

---

## License

按需自用 / 修改。素材版权归原作者所有；请勿将未授权视频提交进仓库。

---

## 房产包装（`douyin_estate` + `dm_estate_cta`）

- 花字：标题 / 卖点 / 价格独立字号；卖点 `join` 或 `stack`  
- CTA：锁定 **花字一体式** `dm_estate_cta`（暖橙 + 星光）；双窗 + 可配置短文案  
- 换色贴纸 id 与总览预览：见 **`assets/README.md`**

```yaml
pack:
  layout:
    highlights_mode: join
  text_motion:
    enter: fade
    bounce_px: 0
    sparkle_anim: true
  sticker:
    style: dm_estate_cta
    text: "私信了解"
    start: 4.5
    duration: 2.5
    repeat_at_end: true
    end_lead: 2.8
    enter: slide_up
    enter_ms: 280
    width_rel: 0.30
```

旧版 `text.lines` 仍兼容（首行标题、末行价格、中间卖点）。

测试：

```bash
python -m pytest tests/test_core.py -q
# 或
python -m unittest discover -v
```
