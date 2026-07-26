# 成片 BGM（短视频纯音乐）

## 说明

抖音/快手等平台上的「热门原曲」受版权保护，**不能**合法批量下载到仓库。  
本目录提供 **6 首气质相近的免版税纯音乐**（轻、不突兀、适合看房成片），来源：

| id | 文件 | 气质 | 原曲名 | 作者 |
|----|------|------|--------|------|
| `carefree` | `bgm_carefree.mp3` | 轻松明亮（默认） | Carefree | Kevin MacLeod |
| `easy_lemon` | `bgm_easy_lemon.mp3` | 柔和俏皮 | Easy Lemon | Kevin MacLeod |
| `life_of_riley` | `bgm_life_of_riley.mp3` | 温暖愉快 | Life of Riley | Kevin MacLeod |
| `summer_day` | `bgm_summer_day.mp3` | 夏日通透 | Summer Day | Kevin MacLeod |
| `dreamlike` | `bgm_dreamlike.mp3` | 柔和梦幻 | Dreamlike | Kevin MacLeod |
| `bittersweet` | `bgm_bittersweet.mp3` | 轻情绪叙事 | Bittersweet | Kevin MacLeod |

- 作者站点：https://incompetech.com/  
- 许可：**Creative Commons Attribution 4.0 (CC BY 4.0)**  
- 发布视频时请在简介/评论区保留署名，例如：  
  `BGM: Carefree by Kevin MacLeod (incompetech.com) · CC BY`

已做 loudnorm 响度对齐，成片里更稳。

## 配置用法

`1.edit.yaml` / `config.yaml`：

```yaml
audio:
  bgm: "carefree"          # 预设 id
  # bgm: "easy_lemon"
  # bgm: "random"          # 每次随机一首内置
  # bgm: "assets/music/bgm_summer_day.mp3"  # 或自备路径
  volume: 0.80
  fade_in: 0.5
  fade_out: 0.8
```

列出全部：

```bash
python edit_speed.py --list-styles
```

## 重新下载

```bash
python scripts/bootstrap_assets.py
# 或只拉音乐：
python scripts/fetch_bgm.py
```

## 自备「真·抖音热门」

若你有平台授权或自己购买的曲库文件，放到本目录后：

```yaml
audio:
  bgm: "assets/music/your_track.mp3"
```
