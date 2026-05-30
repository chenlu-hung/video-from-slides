#!/bin/bash
set -e

# ──────────────────────────────────────────────
# lecture-notes plugin 安裝腳本
# 安裝系統依賴並註冊 Claude Code plugin
# ──────────────────────────────────────────────

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()  { echo -e "${BOLD}==>${RESET} $1"; }
ok()    { echo -e "${GREEN}✓${RESET} $1"; }
warn()  { echo -e "${YELLOW}⚠${RESET} $1"; }
fail()  { echo -e "${RED}✗${RESET} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1. 系統檢查 ─────────────────────────────

info "檢查系統環境..."

# macOS
[[ "$(uname)" == "Darwin" ]] || fail "此工具僅支援 macOS"

# macOS 版本 >= 14
MACOS_VER="$(sw_vers -productVersion)"
MACOS_MAJOR="$(echo "$MACOS_VER" | cut -d. -f1)"
if [[ "$MACOS_MAJOR" -ge 14 ]]; then
    ok "macOS $MACOS_VER"
else
    fail "需要 macOS 14+，目前為 $MACOS_VER"
fi

# ── 2. Homebrew ──────────────────────────────

info "檢查 Homebrew..."
if command -v brew &>/dev/null; then
    ok "Homebrew 已安裝"
else
    info "安裝 Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# ── 3. ffmpeg ────────────────────────────────

info "檢查 ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')"
else
    info "安裝 ffmpeg..."
    brew install ffmpeg
    ok "ffmpeg 安裝完成"
fi

# ── 4. uv 與 TTS 引擎 (mlx-audio / VoxCPM2 + f5-tts-mlx) ────────────

info "檢查 uv..."
if command -v uv &>/dev/null; then
    ok "uv $(uv --version 2>&1 | awk '{print $NF}')"
else
    info "安裝 uv..."
    brew install uv
fi

PLUGIN_DATA_DIR="${HOME}/.local/share/lecture-notes"
TTS_DIR="$PLUGIN_DATA_DIR/tts-py"

info "準備 TTS 引擎於 $TTS_DIR ..."
mkdir -p "$TTS_DIR"
if [[ ! -f "$TTS_DIR/pyproject.toml" ]]; then
    (cd "$TTS_DIR" && uv init --bare --no-readme --no-pin-python >/dev/null)
fi
# 預設 TTS 引擎：mlx-audio（VoxCPM2）。f5-tts-mlx 仍保留以便比較。
# 注意：VoxCPM2 架構目前只在 mlx-audio 的 GitHub main，尚未發行到 PyPI（最新 0.4.3 只有 voxcpm v1），
# 因此暫時從 git 安裝。待 voxcpm2 進入正式 release 後可改回 `uv add mlx-audio`。
(cd "$TTS_DIR" && uv add "mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git" f5-tts-mlx)

cp "$SCRIPT_DIR/lecture-notes/tts/"*.py "$TTS_DIR/"

if (cd "$TTS_DIR" && uv run --quiet python -c "import mlx_audio, soundfile, numpy" 2>/dev/null); then
    ok "mlx-audio (VoxCPM2) 已就緒"
else
    fail "mlx-audio 環境準備失敗"
fi

# ── 5. Claude Code CLI ──────────────────────

info "檢查 Claude Code CLI..."
if command -v claude &>/dev/null; then
    CLAUDE_VER="$(claude --version 2>&1 | head -1)"
    ok "Claude Code $CLAUDE_VER"
else
    fail "找不到 Claude Code CLI。請先安裝: https://claude.ai/code"
fi

# ── 6. 註冊 plugin marketplace 並安裝 ────────

info "註冊 plugin marketplace..."

if claude plugin marketplace list 2>&1 | grep -q "video-from-slides"; then
    ok "marketplace 已註冊，更新中..."
    claude plugin marketplace update video-from-slides
else
    claude plugin marketplace add "$SCRIPT_DIR"
    ok "marketplace 註冊完成"
fi

info "安裝 lecture-notes plugin..."
if claude plugin list 2>&1 | grep -q "lecture-notes@video-from-slides"; then
    ok "lecture-notes plugin 已安裝，更新中..."
    claude plugin update lecture-notes@video-from-slides
else
    claude plugin install lecture-notes@video-from-slides
    ok "lecture-notes plugin 安裝完成"
fi

# ── 完成 ─────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}安裝完成！${RESET}"
echo ""
echo "可用的 skill："
echo "  /lecture-notes <slides.pdf>        從投影片生成 SRT 講稿"
echo "  /video-from-slides <slides-dir>    生成教學影片（自動 TTS + 投影片 → MP4）"
echo ""
echo "快速開始："
echo "  1. 開啟 Claude Code"
echo "  2. /lecture-notes path/to/slides.pdf       # 生成 outline.md 與 srt/"
echo "  3. 在 slides 目錄放入參考人聲："
echo "       voice/ref.wav   （24kHz mono、5–10 秒）"
echo "       voice/ref.txt   （該段語音的逐字稿）"
echo "  4. /video-from-slides path/to/slides-dir    # 自動以 VoxCPM2 合成旁白並產出影片"
echo ""
echo "提示：第一次執行會下載 VoxCPM2-8bit MLX 模型（約 3.2 GB），請保持網路連線。"
echo "若 audio/slide_XX.mp3 已存在則跳過 TTS，可用自製音檔。"
echo ""
