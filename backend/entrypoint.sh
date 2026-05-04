#!/bin/bash
# Start Xvfb virtual display, then run the app

# Clean up Chrome singleton locks (prevents "profile in use" errors)
rm -f /app/chrome-profile/SingletonLock /app/chrome-profile/SingletonCookie /app/chrome-profile/SingletonSocket

# Start dbus
mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || true

# Start Xvfb on display :99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
XVFB_PID=$!
export DISPLAY=:99

echo "Xvfb started on :99 (pid=$XVFB_PID)"

# Wait for Xvfb to be ready
sleep 2

# Verify Xvfb is running
if kill -0 $XVFB_PID 2>/dev/null; then
    echo "Xvfb is running, DISPLAY=$DISPLAY"
else
    echo "ERROR: Xvfb failed to start!"
    exit 1
fi

# Run uvicorn
exec python -m uvicorn app.main:app \
    --host ${FC_HOST:-0.0.0.0} \
    --port ${FC_PORT:-8899}
