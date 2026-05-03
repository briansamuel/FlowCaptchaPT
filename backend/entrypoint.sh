#!/bin/bash
# Start Xvfb virtual display, then run the app

# Install dbus if missing
if [ ! -S /run/dbus/system_bus_socket ]; then
    mkdir -p /run/dbus
    dbus-daemon --system --fork 2>/dev/null || true
fi

# Start Xvfb on display :99
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!
export DISPLAY=:99

echo "Xvfb started on :99 (pid=$XVFB_PID)"

# Wait for Xvfb to be ready
sleep 2

# Run uvicorn
exec python -m uvicorn app.main:app \
    --host ${FC_HOST:-0.0.0.0} \
    --port ${FC_PORT:-8899}
