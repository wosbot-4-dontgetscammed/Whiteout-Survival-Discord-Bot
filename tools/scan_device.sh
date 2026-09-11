#!/bin/bash
#
# Control the dedicated scanner emulator (Pixel7_Scan, port 5560).
#
# It is deliberately NOT run continuously: a booted emulator costs ~45% of one
# CPU even while idle, so the scanner boots it for a run and shuts it down
# afterwards. The autopilot's own emulators are never touched by this script.
#
#   ./tools/scan_device.sh start     boot it (waits for boot_completed)
#   ./tools/scan_device.sh stop      shut it down
#   ./tools/scan_device.sh status    running? CPU cost?
#   ./tools/scan_device.sh app       launch Whiteout Survival on it
#
set -u

AVD="Pixel7_Scan"
PORT=5560
SERIAL="emulator-${PORT}"
PKG="com.gof.global"

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"

is_running() {
    adb devices | grep -q "^${SERIAL}[[:space:]]*device$"
}

case "${1:-status}" in
    start)
        if is_running; then
            echo "$SERIAL already running"
            exit 0
        fi
        echo "booting $AVD on port $PORT ..."
        # -gpu host      : render on the Mac GPU (swiftshader costs ~10x the CPU)
        # -cores 2       : leave headroom for the autopilot's three emulators
        # -no-snapshot   : predictable cold state for every scan run
        nohup emulator -avd "$AVD" -port "$PORT" \
            -no-window -no-audio -no-boot-anim \
            -gpu host -cores 2 -memory 3072 -no-snapshot \
            >/tmp/wos_scan_emu.log 2>&1 &
        until adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do
            sleep 5
        done
        echo "$SERIAL booted"
        ;;
    stop)
        if is_running; then
            adb -s "$SERIAL" emu kill >/dev/null 2>&1
            echo "$SERIAL stopped"
        else
            echo "$SERIAL not running"
        fi
        ;;
    app)
        if ! is_running; then
            echo "$SERIAL not running - start it first" >&2
            exit 1
        fi
        adb -s "$SERIAL" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
        echo "launched $PKG on $SERIAL"
        ;;
    status)
        if is_running; then
            pid=$(pgrep -f "avd $AVD" | head -1)
            cpu=$(ps -Ao pid,pcpu | awk -v p="$pid" '$1==p{print $2"%"}')
            echo "$SERIAL running (pid ${pid:-?}, cpu ${cpu:-?})"
        else
            echo "$SERIAL stopped (0% cpu)"
        fi
        ;;
    *)
        echo "usage: $0 {start|stop|app|status}" >&2
        exit 1
        ;;
esac
