#!/bin/bash
# Setup persistent Chrome + Xvfb for AT&T automation
# Run once per boot, or when Chrome/Xvfb have died

set -e

CHROME_PROFILE="${HOME}/.att-chrome-profile"
DISPLAY_NUM=99
CDP_PORT=9222

echo "=== AT&T Chrome Setup ==="

# 1. Xvfb
if pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null 2>&1; then
    echo "✓ Xvfb already running on :${DISPLAY_NUM}"
else
    echo "Starting Xvfb on :${DISPLAY_NUM}..."
    if tmux has-session -t xvfb 2>/dev/null; then
        tmux kill-session -t xvfb
    fi
    tmux new-session -d -s xvfb
    tmux send-keys -t xvfb "Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 &" Enter
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
    if tmux has-session -t att-chrome 2>/dev/null; then
        tmux kill-session -t att-chrome
    fi
    tmux new-session -d -s att-chrome
    tmux send-keys -t att-chrome "DISPLAY=:${DISPLAY_NUM} setsid google-chrome-stable \
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
        echo "✗ Chrome failed to start — check tmux att-chrome"
        exit 1
    fi
fi

echo ""
echo "Ready for AT&T automation."
echo "  CDP endpoint: http://127.0.0.1:${CDP_PORT}"
echo "  Chrome profile: ${CHROME_PROFILE}"
echo "  Xvfb display: :${DISPLAY_NUM}"
