#!/bin/bash
# rearm_phones.sh - Mainframe-side wireless ADB recovery for the FreeRoam phones.
#
# WHY: the polaris-gateway container's adb is Debian v28 (no mDNS, no TLS), so it
# can only reach a phone whose tcpip 5555 mode is armed - and that mode dies on
# every phone reboot on this ROM. Both phones are TLS-paired to the Mainframe
# (~/platform-tools/adb v37), so their wireless-debugging endpoints reappear on
# mDNS after any reboot: running this on the host restores adb with ZERO USB.
#
#   Rich: FY24031100E6  192.168.50.42  (tls pair guid adb-FY24031100E6-71NED1)
#   Matt: FY24031100BE  192.168.50.98  (tls pair guid adb-FY24031100BE-usU8h8, RedMagic 9)
#
# Usage:
#   ./rearm_phones.sh          # 5555 fast-path -> mDNS TLS fallback -> Matt identity self-heal
#   ./rearm_phones.sh --tcpip  # additionally attempt re-arming tcpip 5555 over the TLS transport
#
# The TLS connect port is DYNAMIC per session (e.g. 33679 today) - pairing trust
# persists across reboots, the port does not; mDNS re-discovery is the fix.
set -u
ADB="${ADB:-$HOME/platform-tools/adb}"
TCP_IP_TOO="${1:-}"

APP_PKG="com.freeroam.tactical"
RICH_IP="192.168.50.42"; RICH_GUID="adb-FY24031100E6-71NED1"
MATT_IP="192.168.50.98"; MATT_GUID="adb-FY24031100BE-usU8h8"

[ -x "$ADB" ] || { echo "❌ adb not found/executable at $ADB"; exit 1; }
"$ADB" start-server >/dev/null 2>&1

# tls_endpoint GUID IP -> echo a connected host:port serial for that phone, or nothing
tls_endpoint() {
    local guid="$1" ip="$2" svc port hp
    svc="$("$ADB" mdns services 2>/dev/null | grep "$guid" | grep '_adb-tls-connect' | head -1)"
    [ -n "$svc" ] || return 0
    port="$(printf '%s\n' "$svc" | sed -E 's/.*:([0-9]+)[[:space:]]*$/\1/')"
    [ -n "$port" ] || return 0
    for hp in "$ip:$port" "$(printf '%s\n' "$svc" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+' | head -1)"; do
        [ -n "$hp" ] || continue
        "$ADB" connect "$hp" >/dev/null 2>&1
        "$ADB" -s "$hp" get-state >/dev/null 2>&1 && { echo "$hp"; return 0; }
    done
}

# rearm LABEL IP GUID -> echoes ONLY the working serial on stdout (captured by
# the caller); all human-readable status goes to stderr. rc 0 = phone reachable.
rearm() {
    local label="$1" ip="$2" guid="$3" serial=""
    if "$ADB" connect "$ip:5555" >/dev/null 2>&1 && "$ADB" -s "$ip:5555" get-state >/dev/null 2>&1; then
        echo "✅ $label: tcpip 5555 alive ($ip:5555)" >&2
        echo "$ip:5555"; return 0
    fi
    serial="$(tls_endpoint "$guid" "$ip")"
    if [ -n "$serial" ]; then
        echo "✅ $label: 5555 dead - mDNS TLS fallback connected → $serial" >&2
        if [ "$TCP_IP_TOO" = "--tcpip" ]; then
            if "$ADB" -s "$serial" tcpip 5555 >/dev/null 2>&1; then
                sleep 2; "$ADB" connect "$ip:5555" >/dev/null 2>&1
                if "$ADB" -s "$ip:5555" get-state >/dev/null 2>&1; then
                    echo "🔁 $label: tcpip 5555 re-armed over the wireless transport" >&2
                    echo "$ip:5555"; return 0
                fi
                echo "⚠️  $label: tcpip re-arm requested but 5555 is not back; staying on $serial" >&2
            else
                echo "⚠️  $label: 'adb tcpip' refused over the TLS transport - USB re-arm still needed" >&2
            fi
        fi
        echo "$serial"; return 0
    fi
    echo "❌ $label: no 5555 and no mDNS TLS endpoint (off-line, or Wireless debugging off?)" >&2
    return 1
}

# identity_selfheal SERIAL - re-assert Matt's runtime grants (revoked by a
# reinstall/clear-data, which reverts the app to Rich/Dashboard identity),
# then relaunch the app if it is not running.
identity_selfheal() {
    local serial="$1" pid=""
    if "$ADB" -s "$serial" shell pm grant "$APP_PKG" android.permission.ACCESS_FINE_LOCATION >/dev/null 2>&1; then
        echo "🔑 Matt: ACCESS_FINE_LOCATION grant asserted (on $serial)"
    else
        echo "⚠️  Matt: grant re-assert failed on $serial - check app install"
    fi
    pid="$("$ADB" -s "$serial" shell pidof "$APP_PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
    if [ -n "$pid" ]; then
        echo "🫀 Matt: app alive (pid $pid)"
    else
        if "$ADB" -s "$serial" shell monkey -p "$APP_PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; then
            echo "🚀 Matt: app was dead - relaunched"
        else
            echo "⚠️  Matt: app dead and relaunch failed - start it manually"
        fi
    fi
}

MATT_SERIAL="$(rearm "Matt" "$MATT_IP" "$MATT_GUID")"; MATT_RC=$?
RICH_SERIAL="$(rearm "Rich" "$RICH_IP" "$RICH_GUID")"; RICH_RC=$?

if [ "${MATT_RC:-1}" -eq 0 ] && [ -n "$MATT_SERIAL" ]; then
    identity_selfheal "$MATT_SERIAL"
fi

echo "---"
echo "Matt: ${MATT_SERIAL:-UNREACHABLE} | Rich: ${RICH_SERIAL:-UNREACHABLE}"
[ "${MATT_RC:-1}" -eq 0 ] || [ "${RICH_RC:-1}" -eq 0 ] || exit 1