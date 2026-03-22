#!/bin/bash
set -e

# ──────────────────────────────────────────────
# lecture-notes plugin 安裝腳本
# 安裝系統依賴、編譯 TTS CLI、註冊 Claude Code plugin
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

# Apple Silicon
ARCH="$(uname -m)"
if [[ "$ARCH" == "arm64" ]]; then
    ok "Apple Silicon ($ARCH)"
else
    warn "偵測到 $ARCH — TTS 語音合成需要 Apple Silicon (arm64)"
fi

# macOS 版本 >= 14
MACOS_VER="$(sw_vers -productVersion)"
MACOS_MAJOR="$(echo "$MACOS_VER" | cut -d. -f1)"
if [[ "$MACOS_MAJOR" -ge 14 ]]; then
    ok "macOS $MACOS_VER"
else
    fail "需要 macOS 14+，目前為 $MACOS_VER"
fi

# ── 2. Xcode 命令列工具 ─────────────────────

info "檢查 Xcode 命令列工具..."
if xcode-select -p &>/dev/null; then
    ok "Xcode 命令列工具已安裝"
else
    info "安裝 Xcode 命令列工具..."
    xcode-select --install
    echo "請在彈出的對話框中點選「安裝」，完成後重新執行此腳本。"
    exit 0
fi

# ── 3. Homebrew ──────────────────────────────

info "檢查 Homebrew..."
if command -v brew &>/dev/null; then
    ok "Homebrew 已安裝"
else
    info "安裝 Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # 載入 brew 到 PATH
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# ── 4. ffmpeg ────────────────────────────────

info "檢查 ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')"
else
    info "安裝 ffmpeg..."
    brew install ffmpeg
    ok "ffmpeg 安裝完成"
fi

# ── 5. 編譯 Swift TTS CLI ────────────────────

TTS_DIR="$SCRIPT_DIR/lecture-notes/scripts/tts"
TTS_BIN="$TTS_DIR/.build/release/TTSInfer"

info "編譯 TTS CLI (TTSInfer)..."
if [[ -f "$TTS_BIN" ]]; then
    ok "TTSInfer 已存在，跳過編譯"
    warn "如需重新編譯，執行: cd $TTS_DIR && swift build -c release"
else
    (cd "$TTS_DIR" && swift build -c release)
    if [[ -f "$TTS_BIN" ]]; then
        ok "TTSInfer 編譯完成"
    else
        fail "TTSInfer 編譯失敗，請檢查錯誤訊息"
    fi
fi

# ── 6. Claude Code CLI ──────────────────────

info "檢查 Claude Code CLI..."
if command -v claude &>/dev/null; then
    CLAUDE_VER="$(claude --version 2>&1 | head -1)"
    ok "Claude Code $CLAUDE_VER"
else
    fail "找不到 Claude Code CLI。請先安裝: https://claude.ai/code"
fi

# ── 7. 註冊 plugin marketplace 並安裝 ────────

info "註冊 plugin marketplace..."

# 檢查是否已註冊
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
echo "  /tts-synthesis <slides-directory>  語音合成（SRT → MP3）"
echo "  /video-from-slides <slides-dir>    生成教學影片（投影片 + 音訊 → MP4）"
echo ""
echo "快速開始："
echo "  1. 開啟 Claude Code"
echo "  2. 輸入 /lecture-notes path/to/slides.pdf"
echo ""
