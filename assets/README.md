# 成片素材目录（assets）

## 目录结构

```
assets/
  fonts/              # 可选自定义字体（优先 SmileySans 等）
  stickers/
    previews/         # 各贴纸 style 静态预览 PNG
    external/noto/    # Noto 动态 emoji GIF 缓存（gitignore，运行时下载）
  music/              # BGM（本地下载；说明见 music/README.md）
  text_previews/      # 花字样式预览 PNG
  style_board/        # 花字 × 贴纸总览拼贴
```

仓库**不包含**输入/成片视频；本地运行后产物在项目根或 `frames/`（均已 gitignore）。

---

## 花字样式（`pack.style`）

```bash
python edit_speed.py --list-styles
```

全部是**短视频多层描边花字**（外描边 → 内描边 → 填充，可选发光/星光），不是字幕条。

| id | 说明 |
|----|------|
| **`douyin_estate`** | **默认** 房产三级：标题 / 卖点 / 价格独立配色与字号 |
| `douyin_fire` | 红字 + 白内边 + 蓝外描 |
| `douyin_pink` | 粉字白描边发光 |
| `douyin_lemon` | 黄字红描边 |
| `douyin_mint` | 薄荷绿 |
| `douyin_gold` | 金粉双描边 |
| `douyin_violet` | 紫粉霓虹 |
| `douyin_sky` | 天蓝 |
| `douyin_candy` | 糖果橙粉 |

### 推荐：房产结构化文案（`douyin_estate`）

```yaml
pack:
  style: douyin_estate
  text:
    title: "绿湖全新未入住"
    highlights:
      - "101平三房"
      - "业主忍痛割爱"
    price: "单价5XXX"
  layout:
    y_rel: 0.255
    max_width_rel: 0.88
    highlights_mode: join   # join = 「A · B」一行；stack = 卖点分行
  text_motion:
    enter: fade             # none | fade | pop
    duration: 0.35
    bounce_px: 0            # 文字跳动；0=关
    pulse: 0                # 透明度呼吸；0=关
    sparkle_anim: true      # 价格旁星光动画（APNG）
```

| 字段 | 含义 |
|------|------|
| `layout.y_rel` | 整块花字中心上下（0=顶 1=底） |
| `layout.max_width_rel` | 最大宽度占画面宽 |
| `layout.highlights_mode` | `join` / `stack` |
| `layout.font_size_rel` | 可选，覆盖样式默认字号 |
| `layout.line_gap_rel` | 可选行距（负值更紧） |
| `text_motion.enter` | 花字入场：`none` / `fade` / `pop` |
| `text_motion.sparkle_anim` | 价格星光闪烁 |

预览：`text_previews/<style>.png`  
兼容旧写法：`text.lines`（首行标题、末行价格、中间卖点）。

---

## 动态贴纸 / CTA（`pack.sticker`）

### 锁定默认

**`dm_estate_cta`**：暖橙**花字一体式**「私信了解」+ [Noto Animated Emoji](https://googlefonts.github.io/noto-emoji-animation/) 星光 `2728`（Apache-2.0）。  
结构：多层花字主体 + 小星光点缀（**不**叠大号气泡/独立字条）。

### 同结构换色 / 点缀

| style | 气质 |
|-------|------|
| `dm_estate_cta` | **锁定默认** 暖橙 + 星光 |
| `dm_estate_cta_gold` | 金奢 |
| `dm_estate_cta_sky` | 晴空蓝 |
| `dm_estate_cta_mint` | 薄荷 |
| `dm_estate_cta_violet` | 紫霓 |
| `dm_estate_cta_snow` | 冰雪白 + 金环 |
| `dm_estate_cta_heart` | 暖粉 + 爱心 GIF |
| `dm_estate_cta_fire` | 炽红强钩子 |
| `dm_estate_cta_ink` | 墨黑 + 金环 |
| `dm_estate_cta_lemon` | 柠檬黄 + 红描边 |
| `dm_estate_cta_neon` | 霓虹青 |
| `dm_estate_cta_bubble` | 气泡主视觉（备选） |
| `dm_estate_cta_plain` | 软珊瑚胶囊（无网络 GIF） |

### 旧 id（已升为花字一体式，保留兼容）

| style | 现效果 |
|-------|--------|
| `dm_follow_me` | 青字「私信我」+ 星光 |
| `dm_pink_wave` | 粉字「私信我」+ 星光 |
| `dm_chat_pop` | 粉字「私信我」+ 爱心 |
| `dm_heart_tap` | 短字「私信」+ 爱心 |
| `dm_bell_cute` | 金字「私信我」+ 星光 |
| `dm_hand_cute` | 「戳我私信」+ 星光 |
| `dm_emoji_*` | 一体角标：emoji + 文案芯片 |

### 配置字段

| 字段 | 含义 |
|------|------|
| `style` | 上表任一 id |
| `text` | 短文案：`私` / `私信` / `私信我` / `私信了解` |
| `enter` | 入场：`slide_up`（默认）/ `pop` / `none` |
| `enter_ms` | 入场时长 ms（默认 280） |
| `start` / `duration` | 首窗开始与时长 |
| `repeat_at_end` / `end_lead` | 片尾再出与片尾窗长 |
| `x` / `y` | 中心锚点 0~1 |
| `width_rel` | 宽度占画面宽（花字系约 0.30） |
| `file` | 自备透明 APNG/GIF/WebM 时填路径（忽略绘制） |
| `fps` | 贴纸合成帧率（默认 12） |

```yaml
sticker:
  style: dm_estate_cta
  text: "私信了解"
  enter: slide_up
  enter_ms: 280
  start: 4.5
  duration: 2.5
  repeat_at_end: true
  end_lead: 2.8
  width_rel: 0.30
  x: 0.16
  y: 0.90
```

短视频默认双窗：**中段** `start`～`start+duration`，**片尾** `end_lead` 秒；两窗过近时只保留片尾。

### 预览与缓存

| 路径 | 说明 |
|------|------|
| `stickers/previews/<style>.png` | 单 style 预览 |
| `style_board/all_styles_board.png` | 花字 + 贴纸总览（若已生成） |
| `style_board/stickers_board_v2.png` | 贴纸专项总览（若已生成） |
| `stickers/external/noto/*_512.gif` | Noto GIF 缓存（gitignore） |

---

## 音频（BGM）

与 `assets/music/` **磁盘文件一致**（详见 **`music/README.md`**）。默认 `pack.audio.bgm: random` 会扫描库内现有音轨。

| 组 | 配置 id | 说明 |
|----|---------|------|
| local | `a1` … `a18`（缺号跳过） | `a*.m4a` |
| shortlist | `sl01` … | `shortlist/*.mp3`（若有） |
| curated | `pop_hook` 等 | `curated/*.mp3`（若有） |
| random | `random` | 随机选库内存在的文件 |
| 路径 | 相对/绝对文件路径 | 直接指定 mp3/m4a |

```yaml
audio:
  bgm: "random"      # 或 a5 / shortlist/01.mp3 / 路径
  volume: 0.80       # 无口播时 BGM 音量
```

有 **TTS 口播** 时，BGM 用 `pack.voiceover.bgm_under_voice`（默认 **0.40**）全程恒定垫底，与口播 `amix`，**不做** sidechain / 动态闪避。

```bash
python edit_speed.py --list-styles   # 含 BGM 列表
```

---

## 口播与字幕（可选）

见根目录 `README.md` 与 `examples/sample.edit.yaml`：

- `pack.voiceover` / `pack.captions`（默认关）
- 字幕：固定底中锚点 ASS；口播文案与上屏文案分轨（弱标点不上屏）
- 贴纸：`schedule: after_voice` 可避免与口播抢同一时段

```bash
pip install edge-tts   # 可选，真人声
```

---

## 常用命令

```bash
python scripts/bootstrap_assets.py          # 字体 + 贴纸依赖 + BGM
python edit_speed.py path/to/input.mp4 --pack
python edit_speed.py path/to/input.mp4 --pack-only   # 只重做包装
python edit_speed.py --list-styles
```

### 贴纸文件策略

- **仓库不提交**大体积 APNG 成品（运行时由 `stickers_gen` 按 `style` + 文案生成到工作目录或本地缓存）。
- **可提交**：`stickers/previews/*.png`、`style_board/*`、`text_previews/*` 等小预览图，方便选 id。
- Noto 动态 emoji 首次生成 CTA 时下载到 `stickers/external/noto/`（已 gitignore）。

完整每视频示例：`examples/sample.edit.yaml`。  
根目录 **`README.md`** 含变速调参、CLI 与 FAQ。
