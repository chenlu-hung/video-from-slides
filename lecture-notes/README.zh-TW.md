# lecture-notes

從 PDF 或 TeX 投影片自動生成講稿、語音合成，並製作含旁白的教學影片。全程在 Apple Silicon 上本地運行。

## 功能

- 讀取 PDF 或 TeX 投影片，自動估算每頁講述時間
- 生成可編輯的 `outline.md` 大綱，供確認後再產生講稿
- 以平行 agent 批次生成 SRT 格式講稿（每批 1–5 頁）
- 自動驗證內容覆蓋度、SRT 格式正確性及時間準確性
- 支援中文與英文投影片
- 使用 CosyVoice 3 (Swift/CoreML) 或 Qwen3-TTS (Python/MLX) 進行語音合成
- 聲音複製：支援單一參考音檔或多段音檔目錄（多段平均 embedding，效果更佳）
- 支援預先計算的 speaker embedding（`--embedding` 參數），加速重複合成
- 自動偵測語言（中文／英文）
- 生成含 Ken Burns 效果的教學影片，自動合併音訊與投影片

## 安裝

```bash
git clone https://github.com/chenlu-hung/video-from-slides.git
cd video-from-slides
./install.sh
```

安裝腳本會自動檢查並安裝所有依賴（Xcode 命令列工具、Homebrew、ffmpeg）、編譯 Swift TTS CLI，並將 plugin 註冊到 Claude Code。

## 系統需求

- Claude Code CLI
- macOS 14+ Apple Silicon（語音合成及影片生成）
- Xcode 命令列工具（編譯 Swift TTS CLI）
- ffmpeg（影片生成及 Python TTS fallback 需要，`brew install ffmpeg`）

## 使用方式

### 步驟一：從投影片生成 SRT 講稿

```
/lecture-notes path/to/slides.pdf
```

### 步驟二：語音合成

Swift TTS CLI 由 `install.sh` 自動編譯。如需手動重新編譯：

```bash
cd lecture-notes/scripts/tts && swift build -c release
```

然後執行：

```
/tts-synthesis path/to/slides-directory
```

#### 聲音複製選項

- **單一音檔**：在投影片目錄放置一個 3–10 秒的單聲道 WAV 檔案，命名為 `voice_ref.wav`
- **多段音檔**（推薦）：將多個音檔放在 `voice_refs/` 目錄中，CLI 會自動過濾靜音片段、計算多段平均 embedding 並重新正規化 L2 norm，效果更穩定
- **預先計算 embedding**：用 `--save-embedding speaker.json` 存檔後，之後以 `--embedding speaker.json` 直接載入，跳過 speaker 模型載入

```bash
# 從多段音檔計算平均 embedding 並存檔
TTSInfer --srt slide.srt --output slide.mp3 \
  --voice-ref ./voice_refs/ --save-embedding speaker.json

# 直接使用已儲存的 embedding（更快）
TTSInfer --srt slide.srt --output slide.mp3 --embedding speaker.json
```

### 步驟三：生成教學影片

```
/video-from-slides path/to/slides-directory
```

可選擇合併所有投影片為一支影片，或按章節分段合併。

## 工作流程

### 講稿生成（`/lecture-notes`）

1. **大綱** — 讀取投影片，估算時長，生成 `outline.md` 供審閱
2. **生成** — 確認大綱後，啟動 agent 平行生成 `srt/slide_XX.srt`
3. **審查** — 驗證所有 SRT 檔案並回報問題

### 語音合成（`/tts-synthesis`）

1. **準備** — 確認 SRT 檔案、檢查 CLI 是否已編譯、偵測聲音參考檔
2. **合成** — 平行啟動 agent 將每個 SRT 轉為 MP3
3. **驗證** — 交叉比對輸出、檢查檔案大小，可重試失敗項目

### 影片生成（`/video-from-slides`）

1. **準備** — 確認 SRT 及音訊檔案存在、PDF 轉 PNG、解析章節結構
2. **生成** — 平行啟動 agent，為每頁製作 Ken Burns 效果影片並合併音訊
3. **合併** — 選擇合併策略（全部合併 / 按章節 / 兩者皆要）

## 輸出結構

```
your-slides-directory/
├── slides.pdf
├── outline.md
├── voice_ref.wav        （選用，聲音複製）
├── srt/
│   ├── slide_01.srt
│   ├── slide_02.srt
│   └── ...
├── audio/
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
    └── final_all.mp4    （或 section_XX_name.mp4）
```

## TTS 後端

| 後端 | CLI | 模型 | 備註 |
|------|-----|------|------|
| CoreML（預設） | Swift `TTSInfer` | CosyVoice 3（透過 [speech-swift](https://github.com/soniqo/speech-swift)） | Apple Silicon 最快，使用 Neural Engine |
| MLX | Swift `TTSInfer` | CosyVoice 3（透過 [speech-swift](https://github.com/soniqo/speech-swift)） | 加 `--backend mlx` 參數 |
| Python fallback | `fallback/tts_infer.py` | Qwen3-TTS 0.6B | 需 `pip install mlx-audio`，首次自動下載模型 |

## TTSInfer CLI 參數

```
TTSInfer --srt <路徑> --output <路徑>
  [--voice-ref <檔案|目錄>]       聲音參考（單一檔案或多檔目錄）
  [--embedding <路徑.json>]       載入預先計算的 speaker embedding
  [--save-embedding <路徑.json>]  將計算好的 embedding 存檔
  [--language <auto|chinese|english>]  語言（預設：自動偵測）
  [--instruction <文字>]          CosyVoice3 Instruct 風格指令
  [--backend coreml|mlx]          推論後端（預設：coreml）
```
