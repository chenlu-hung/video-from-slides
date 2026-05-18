# lecture-notes

從 PDF 或 TeX 投影片自動生成講稿，使用 [f5-tts-mlx](https://github.com/lucasnewman/f5-tts-mlx)（Python，透過 [uv](https://github.com/astral-sh/uv) 執行）在本機合成旁白，並輸出含 Ken Burns 效果的教學影片。

## 功能

- 讀取 PDF 或 TeX 投影片，自動估算每頁講述時間
- 生成可編輯的 `outline.md` 大綱，供確認後再產生講稿
- 以平行 agent 批次生成 SRT 格式講稿（每批 1–5 頁）
- 自動驗證內容覆蓋度、SRT 格式正確性及時間準確性
- 支援中英文投影片，亦支援同一張投影片內中英夾雜
- 以 f5-tts-mlx（Python + MLX、Apple Silicon）合成旁白，可用專案提供的人聲樣本進行 voice cloning，並對齊每頁 SRT 總長度
- 生成含 Ken Burns 效果的教學影片，自動合併音訊與投影片

## 安裝

```bash
git clone https://github.com/chenlu-hung/video-from-slides.git
cd video-from-slides
./install.sh
```

安裝腳本會自動檢查/安裝依賴（Homebrew、ffmpeg、uv），在 `~/.local/share/lecture-notes/tts-py/` 建立 uv 專案並安裝 `f5-tts-mlx`，並把 plugin 註冊到 Claude Code。

## 系統需求

- macOS 14+ 且為 Apple Silicon（MLX 需求）
- Claude Code CLI
- ffmpeg（`brew install ffmpeg`）
- uv（`brew install uv`）

## 使用方式

### 步驟一：從投影片生成 SRT 講稿

```
/lecture-notes path/to/slides.pdf
```

### 步驟二：放入參考人聲

在投影片目錄底下建立：

- `voice/ref.wav` — 24kHz mono WAV，目標講者 5–10 秒語音樣本
- `voice/ref.txt` — `ref.wav` 對應的逐字稿

如需轉檔可用 ffmpeg：

```bash
ffmpeg -i source.m4a -ac 1 -ar 24000 voice/ref.wav
```

若想對部分投影片改用自製音檔，直接把 `audio/slide_XX.mp3` 放好即可——已存在的 MP3 不會被覆寫，TTS 只會補齊缺漏的頁。

### 步驟三：生成教學影片

```
/video-from-slides path/to/slides-directory
```

首次執行會下載 f5-tts MLX 模型（約 1.5 GB），之後會使用快取。可選擇合併所有投影片為一支影片，或按章節分段合併。

## 工作流程

### 講稿生成（`/lecture-notes`）

1. **大綱** — 讀取投影片，估算時長，生成 `outline.md` 供審閱
2. **生成** — 確認大綱後，啟動 agent 平行生成 `srt/slide_XX.srt`
3. **審查** — 驗證所有 SRT 檔案並回報問題

### 影片生成（`/video-from-slides`）

1. **準備** — 檢查 SRT、參考人聲與 TTS 執行檔；PDF 轉 PNG；解析章節
2. **TTS** —（`audio/` 已備齊時跳過）啟動 `tts-synthesizer` agent，逐頁呼叫 `python -m f5_tts_mlx.generate`（透過 uv）並對齊每頁 SRT 總長度
3. **合成影片** — 平行啟動 agent，為每頁製作 Ken Burns 影片並合併音訊
4. **合併** — 選擇合併策略（全部合併 / 按章節 / 兩者皆要）

## 輸出結構

```
your-slides-directory/
├── slides.pdf
├── outline.md
├── voice/
│   ├── ref.wav             （參考人聲，24kHz mono）
│   └── ref.txt             （ref.wav 的逐字稿）
├── srt/
│   ├── slide_01.srt
│   ├── slide_02.srt
│   └── ...
├── audio/                  （由 f5-tts-mlx 自動合成，或自備）
│   ├── slide_01.mp3
│   ├── slide_02.mp3
│   └── ...
├── images/
│   ├── slide_01.png
│   ├── slide_02.png
│   └── ...
└── video/
    ├── slide_01.mp4
    ├── slide_02.mp4
    ├── ...
    └── final_all.mp4       （或 section_XX_name.mp4）
```
