#!/bin/bash
# Setup persistent Chrome + Xvfb for Certify/Emburse automation
# Reuses the same infrastructure as att-invoices (shared Xvfb + Chrome)
# Run once per boot, or when Chrome/Xvfb have died

set -e

CHROME_PROFILE="${CERTIFY_CHROME_PROFILE:-${HOME}/.certify-chrome-profile}"
DISPLAY_NUM="${CERTIFY_DISPLAY:-99}"
CDP_PORT="${CERTIFY_CDP_PORT:-9222}"
TMUX_XVFB="${CERTIFY_TMUX_XVFB:-xvfb}"
TMUX_CHROME="${CERTIFY_TMUX_CHROME:-certify-chrome}"

echo "=== Certify/Emburse Chrome Setup ==="

# 1. Xvfb
if pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null 2>&1; then
    echo "✓ Xvfb already running on :${DISPLAY_NUM}"
else
    echo "Starting Xvfb on :${DISPLAY_NUM}..."
    if tmux has-session -t "${TMUX_XVFB}" 2>/dev/null; then
        tmux kill-session -t "${TMUX_XVFB}"
    fi
    tmux new-session -d -s "${TMUX_XVFB}"
    tmux send-keys -t "${TMUX_XVFB}" "Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 &" Enter
    sleep 2
    if pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null 2>&1; then
        echo "✓ Xvfb started"
    else
        echo "✗ Xvfb failed to start"
        exit 1
    fi
fi

# 2. Chrome
if curl -s "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "✓ Chrome already running on CDP port ${CDP_PORT}"
    curl -s "http://127.0.0.1:${CDP_PORT}/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Browser: {d.get(\"Browser\",\"?\")}')" 2>/dev/null || true
else
    echo "Starting Chrome with CDP on port ${CDP_PORT}..."
    mkdir -p "${CHROME_PROFILE}"
    if tmux has-session -t "${TMUX_CHROME}" 2>/dev/null; then
        tmux kill-session -t "${TMUX_CHROME}"
    fi
    tmux new-session -d -s "${TMUX_CHROME}"
    tmux send-keys -t "${TMUX_CHROME}" "DISPLAY=:${DISPLAY_NUM} setsid google-chrome-stable \
        --remote-debugging-port=${CDP_PORT} \
        --user-data-dir=${CHROME_PROFILE} \
        --disable-blink-features=AutomationControlled \
        --no-first-run \
        --no-default-browser-check \
        --disable-dev-shm-usage \
        --disable-gpu \
        --no-sandbox \
        --window-size=1920,1080 \
        about:blank &" Enter
    sleep 3
    if curl -s "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        echo "✓ Chrome started"
    else
        echo "✗ Chrome failed to start — check tmux ${TMUX_CHROME}"
        exit 1
    fi
fi

echo ""
echo "Ready for Certify automation."
echo "  CDP endpoint: http://127.0.0.1:${CDP_PORT}"
echo "  Chrome profile: ${CHROME_PROFILE}"
echo "  Xvfb display: :${DISPLAY_NUM}"
