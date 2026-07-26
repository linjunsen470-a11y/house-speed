# 成片素材目录（assets）

## 目录结构

```
assets/
  fonts/           # 可选自定义字体
  stickers/        # 动态私信贴纸（.apng）
  music/           # BGM（mp3 本地下载；说明见 music/README.md）
  text_previews/   # 花字样式预览
```

## 花字样式（`pack.style`）

```bash
python edit_speed.py --list-styles
```

全部是**短视频多层描边花字**（外描边 + 内描边 + 填充渐变 + 发光 + 星光），不是字幕条。

| id | 说明 |
|----|------|
| `douyin_fire` | 红字 + 白内边 + 蓝外描（默认，对齐爆款参考） |
| `douyin_pink` | 粉字白描边发光 |
| `douyin_lemon` | 黄字红描边 |
| `douyin_mint` | 薄荷绿 |
| `douyin_gold` | 金粉双描边 |
| `douyin_violet` | 紫粉霓虹 |
| `douyin_sky` | 天蓝 |
| `douyin_candy` | 糖果橙粉 |

推荐 4 行文案 + 位置：

```yaml
pack:
  style: douyin_fire
  text:
    lines:
      - "绿湖全新未入住"
      - "101平三房"
      - "业主忍痛割爱"
      - "单价5XXX"
  layout:
    y_rel: 0.30            # 字块中心：0=顶 1=底
    max_width_rel: 0.90
    # font_size_rel: 0.082 # 字号（相对画面短边）
    # line_gap_rel: -0.006 # 行距；0 更紧，负值再贴紧
```

| 字段 | 含义 |
|------|------|
| `layout.y_rel` | 整块花字中心的上下位置 |
| `layout.font_size_rel` | 字号（可选，覆盖样式默认） |
| `layout.line_gap_rel` | 行距（可选；负值更紧） |
| `layout.max_width_rel` | 最大宽度占画面宽 |

预览图：`text_previews/<style>.png`

## 动态贴纸（`pack.sticker.style`）

推荐（Noto 透明动态 emoji + 中文角标，Apache-2.0）：

| id | 味道 | 默认位置 |
|----|------|----------|
| `dm_emoji_bubble` | 聊天气泡 💬 + 私信我（默认） | 左下 |
| `dm_emoji_heart` | 爱心 ❤️ + 私信我 | 左下 |
| `dm_emoji_mail` | 信封 💌 + 私信我 | 左下 |
| `dm_emoji_point` | 指向 👉 + 戳我私信 | 左下 |

程序绘制备用：`dm_follow_me` / `dm_pink_wave` / `dm_chat_pop` / `dm_heart_tap` / `dm_bell_cute` / `dm_hand_cute`

从 `start` 秒出现到片尾，循环播放。

```yaml
sticker:
  style: dm_emoji_bubble
  start: 5.0             # 第几秒开始出现（到片尾）
  width_rel: 0.34        # 贴纸宽度
  x: 0.16                # 中心水平：左下≈0.16 / 右下≈0.84
  y: 0.90                # 中心垂直：越接近 1 越靠底
```

也可自备透明 APNG/WebM/GIF：`sticker.file: "path/to/sticker.apng"`

重新生成内置贴纸：

```bash
python -c "from pathlib import Path; from walkthrough_edit.stickers_gen import ensure_builtin_stickers; ensure_builtin_stickers(Path('assets/stickers'), force=True)"
```

## 音频（BGM）

内置 6 首短视频向纯音乐（Kevin MacLeod / CC BY，详情见 **`music/README.md`**）：

| id | 气质 |
|----|------|
| `carefree` | 轻松明亮（默认） |
| `easy_lemon` | 柔和俏皮 |
| `life_of_riley` | 温暖愉快 |
| `summer_day` | 夏日通透 |
| `dreamlike` | 柔和梦幻 |
| `bittersweet` | 轻情绪叙事 |
| `random` | 随机一首内置 |

```yaml
audio:
  bgm: "carefree"    # 或 easy_lemon / random / 自备路径
  volume: 0.80
```

```bash
python edit_speed.py --list-styles          # 查看曲库
python scripts/fetch_bgm.py                 # 重新下载
```

## 一键

```bash
python edit_speed.py 1.mp4 --pack
python edit_speed.py 1.mp4 --pack-only   # 只重做花字/贴纸/BGM
```
