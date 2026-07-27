# 房产带看视频 · 自动变速剪辑

对竖屏/横屏**带看 walkthrough** 做智能变速：**不删除任何画面**，用本地 CV 规则把「房间 / 过道 / 空墙」分段并改变播放倍率，可选叠花字、私信贴纸与 BGM。

> 全程 **OpenCV + numpy + ffmpeg**，不需要大模型。  
> 仓库只含**代码与配置**；输入视频、成片、`frames/` 缓存、`eval/` 评测产物均不提交。

---

## 环境要求

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) / ffprobe 在 `PATH` 中
- 依赖：

```bash
pip install -r requirements.txt
```

`requirements.txt`：`opencv-python`、`numpy`、`PyYAML`、`Pillow`。

---

## 快速开始

```bash
# 单片变速（默认写出 <stem>_edited.mp4）
python edit_speed.py path/to/input.mp4

# 指定配置与输出
python edit_speed.py path/to/input.mp4 -c config.yaml -o out.mp4

# 只看分段计划（不导出成片）
python edit_speed.py path/to/input.mp4 --dry-run

# 文件名以 "-" 开头时（Windows/argparse），用 -- 分隔
python edit_speed.py -- -1a.mp4 --dry-run

# dry-run + 低分标注代理
python edit_speed.py path/to/input.mp4 --dry-run --review

# 变速 + 包装（花字 + CTA 贴纸 + 仅 BGM）
python edit_speed.py path/to/input.mp4 --pack

# 只改包装，复用 frames/*/speed_raw.mp4
python edit_speed.py path/to/input.mp4 --pack-only

# 列出花字 / 贴纸 / BGM 预设
python edit_speed.py --list-styles

# 首次准备素材（字体 / 贴纸 / BGM）
python scripts/bootstrap_assets.py
```

### 别墅多楼层一键打包

```bash
# 根目录素材：1-包括前花园后花园 / 2b / 3b-主人房 / -1a / -2
python scripts/compile_villa_tour.py

# edited_1/ 目录下的素材集 + 成片
python scripts/process_edited_1.py
```

| 脚本 | 作用 |
|------|------|
| `compile_villa_tour.py` | 根目录五层顺序：1F→2F→3F→-1F→-2F，角标 + 黑转 + 包装 |
| `process_edited_1.py` | 处理 `edited_1/` 全部源片，并打包该目录别墅成片 |

**导出分辨率**：取各源**最小宽高**（当前常见 **540×960**），**不升采样到 1080**，避免「源片 20 多 MB、成片 150MB+」的虚大体积。  
包装默认 **libx264 CRF 23**；另出一档 **CRF 26** 轻量版（不再用「14MB 硬卡 1080p」毁画质）。

---

## 项目结构

```
clip/
├── edit_speed.py                 # CLI 入口
├── evaluate_segments.py          # segments vs 手标评估
├── config.yaml                   # 主配置（速度 / 分类 / 分段 / 包装）
├── requirements.txt
├── README.md
├── assets/                       # 字体 / 贴纸预览 / BGM 说明
├── examples/sample.edit.yaml
├── scripts/
│   ├── bootstrap_assets.py
│   ├── fetch_bgm.py
│   ├── compile_villa_tour.py   # 根目录别墅全流程
│   ├── process_edited_1.py       # edited_1 批处理 + 打包
│   └── audit_bgm.py / …          # BGM 辅助（自用）
├── tests/
│   ├── test_core.py
│   └── test_scene_aba.py         # 场景 A→B→A 检测（实验/评测）
└── walkthrough_edit/
    ├── analyze.py                # 逐帧 mean / std / edge / motion
    ├── classify.py               # 双门控分类 + 段级后处理 + 静止 boost
    ├── config.py
    ├── render.py / pipeline.py
    ├── pack.py                   # 花字 + 贴纸 + BGM
    ├── scene_aba.py              # 外观/光流离题检测（可选评测）
    ├── text_styles.py
    ├── stickers_gen.py
    └── music_catalog.py
```

本地运行会生成（均 gitignore）：

```
frames/<stem>-<hash>/     # motion.csv, segments.json, summary.json, speed_raw…
frames/villa_master/      # 多楼层中间片
edited_1/                 # 另一套素材与成片（本地）
eval/                     # 抽帧评测 / 对比（本地）
*.mp4
```

---

## 流水线

```
输入视频
   │
   ▼
analyze   逐帧：亮度、std、Canny 边缘密度、帧差 motion
   │
   ▼
classify  双门控打标 room / move / fast
   │
   ▼
segments  短段吸收 → 夹心否决 → 平面/过道 promote
          → 长结构 fast 降 move → overrides
          → room 停留衰减 → 近静止 boost
   │
   ▼
render    ffmpeg：trim + setpts + atempo + concat
   │
   ▼
pack（可选）  花字 + CTA + BGM
   │
   ▼
*_edited.mp4
```

### 标签与默认速度（见 `config.yaml`）

| 标签 | 默认速度 | 含义 |
|------|----------|------|
| `room` | **1.35×** | 有信息房间/扫景（可再 dwell 衰减或静止 boost） |
| `move` | **2.65×** | 走廊 / 过渡行走 |
| `fast` | **3.5×** | 空墙 / 急转 / 低信息穿行（≥3.0） |

### 双门控分类（摘要）

1. **低信息平面**（任意颜色墙）：以 **edge 为主**，`edge < wall_edge_max` → `fast`（不要求「白」或低 std）  
2. **高结构保护**：`edge ≥ content_struct_min` → `room`，避免客厅扫景被 3× 甩  
3. **低结构 + 高运动** → `fast`  
4. **中结构持续行走** → `move` / 条件满足时 `fast`  
5. **段级**：  
   - `room—短 fast—room` 且两侧有结构 → 压回 `room`（离题扫视）  
   - 长 fast 且段均 edge 仍高 → 降为 `move`（过道误 3×）  
   - 近静止（motion 低且持续）→ 至少 `static_boost_speed`（默认 2.7×）

### 停留衰减与静止

- `pacing.room_hold_ramp`：同一 room 内随停留时长提速  
- **镜头在动**（motion 高）不做 ramp，减轻甩镜  
- **镜头几乎不动** 的 room 岛：`static_boost_*` 强制提速  

### 阳台 / 外景

高 edge 扫景仍按 `room` 保护；静止高 edge（盯家具）允许衰减/boost。  
不准时用 `overrides` 强制 `kind`。

---

## `config.yaml` 常用调参

### 速度（最常改）

```yaml
speeds:
  room: 1.35
  move: 2.65
  fast: 3.5
```

- 成片仍偏长 → 略提高 `room` / `move`，或加强 `static_boost_speed`  
- 转角太闪 → 略降 `fast`（勿低于 3.0 若产品约束要求）

### 分类 / 分段（防误伤）

| 现象 | 建议 |
|------|------|
| 客厅扫景被 3× | 查 `content_struct_min` / `dash_struct_max` / 夹心否决 |
| 过道整段 3× | 提高 `corridor_fast_motion`；依赖 `structured_fast_demote_*` |
| 阴影墙未加速 | 确认 `flat_*` / `flat_promote_edge_max`；edge 主判 |
| 静止画面仍拖 | 提高 `static_boost_speed` 或降低 `static_motion_max` |
| 1×↔3× 闪切 | 提高 `min_fast_duration` / `sandwich_demote_fast_max` |

### 人工覆盖

```yaml
overrides:
  - start: 10.0
    end: 12.5
    kind: room
```

或单视频 `<stem>.edit.yaml`（已 gitignore，勿提交敏感覆盖）。

### 编码

```yaml
encode:
  match_source: true   # 单片变速：贴近原片码率
  # 别墅 pack 脚本会改用 match_source: false + crf: 23
```

**体积原则**：源多为 **540p 低码/HEVC** 时，不要默认放大到 1080；成片码率宜贴近源片量级。

---

## 调参工作流

1. `python edit_speed.py 素材.mp4 --dry-run` 看 `Out≈` 与 `segments.json`  
2. 改 `config.yaml`  
3. 再 dry-run  
4. 去掉 `--dry-run` 导出预览  
5. 个别秒数用 `overrides` 修  

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `input` | 输入视频（`-` 开头文件名请用 `python edit_speed.py -- -1a.mp4`） |
| `-o` / `--output` | 输出路径 |
| `-c` / `--config` | 默认 `config.yaml` |
| `--dry-run` | 只分析与打印计划 |
| `--review [PATH]` | 低分标注代理 |
| `--reanalyze` | 忽略 motion 缓存 |
| `--pack` / `--pack-only` | 包装成片 |
| `--json` | stdout 仅 JSON |
| `--list-styles` | 花字/贴纸/BGM 列表 |

---

## 测试

```bash
python -m pytest tests/ -q
# 或
python -m unittest discover -v
```

分段手标评估：

```bash
python evaluate_segments.py frames/<stem-hash>/segments.json labels.yaml
```

---

## 设计约束

1. **不丢弃片段**：全程 trim + 变速 + concat。  
2. **规则可解释**：`motion.csv` + `segments.json` 可复盘。  
3. **配置外置**：阈值与倍率在 YAML。  
4. **本地可跑**：无云端大模型依赖。  
5. **仓库无素材**：视频、缓存、eval 网格、本地 `edited_1/` 不进 git。  

---

## 常见问题

**Q: 成片体积远大于源片总和？**  
旧版曾把 540p 升到 1080p + 高 CRF/高码率。当前别墅脚本**对齐源分辨率**；请用最新 `compile_villa_tour.py` / `process_edited_1.py`。

**Q: 文件名 `-1a.mp4` 报错？**  
CLI：`python edit_speed.py -- -1a.mp4`。ffprobe 已用 `-i` 传路径。

**Q: 找不到 ffmpeg？**  
安装并加入 `PATH`，确认 `ffmpeg -version`。

**Q: 导出到一半 Ctrl+C？**  
会尽量结束残留 ffmpeg 并清理未完成的 `.part` 文件。

**Q: `--pack` 比只变速更糊？**  
包装会再编码一层；需要极致画质可只出变速片再叠字幕。

**Q: 音频？**  
变速片可保留原声加速；`--pack` 默认去掉原声、只留 BGM。

---

## 包装（`douyin_estate` + `dm_estate_cta`）

- 花字：标题 / 卖点 / 价格；卖点 `join` 或 `stack`  
- CTA：`dm_estate_cta` 暖橙一体式；中段 + 片尾双窗  
- 换色与预览：见 **`assets/README.md`**；BGM：见 **`assets/music/README.md`**

每视频覆盖示例：`examples/sample.edit.yaml`。

---

## License

按需自用 / 修改。素材版权归原作者；请勿将未授权视频提交进仓库。
