#!/system/bin/sh
# Runs on the Fire TV as the ADB shell user (pushed and kept running by orbit_helper.py on your home server).
#
# 1. Whenever the system opens Amazon's home screen (Home button, or leaving an app), open the Orbit launcher
#    right away. Android holds back other apps for ~5 s after Home (what made Home on Fire slow); the
#    shell user isn't held back.
# 2. When an HDMI input loses its picture (e.g. PS4 goes to Rest Mode), go to the launcher: after 2 s if the
#    picture had been stable for 15 s+, after 10 s if it only just appeared (startup flicker), and after
#    60 s if it never showed one (console still waking). Amazon itself reports the loss ~4.5 s late.
#
# 3. When a music app opens itself (an Alexa voice request, or a phone connecting with Spotify Connect) while you
#    weren't in it, go back to the launcher in idle mode after 2 s: the music keeps playing in the background.
#    Music apps: Spotify and Amazon Music. Opening one yourself (from Orbit's tile or Amazon's home) is left alone.
# 4. "Alexa, play …" (Amazon Music) plays through Alexa's own player, which opens a full-screen now-playing
#    screen (APLActivity). Same thing: back to the launcher in idle mode after 2 s, but only if music is
#    playing, so Alexa's other full-screen answers stay put.
#
# Triggers on logcat lines, which (unlike piped getevent output) arrive immediately.
LAUNCHER=home.jw.orbit/.OrbitActivity   # was me.efesser.flauncher/.MainActivity (FLauncher, still installed) until 2026-10-27
GEN=/data/local/tmp/homewatch.gen   # bumped on every signal change; a pending "go home" only fires if unchanged
LOST_DELAY=2        # picture was stable (>= SETTLE s) and then went away
UNSETTLED_DELAY=10  # picture appeared < SETTLE s ago (startup / mode switch flicker)
SETTLE=15
NEVER_DELAY=60
MUSIC_APPS="com.spotify.tv.android com.amazon.bueller.music"
ALEXA_PLAYER=com.amazon.alexamediaplayer.runtime.ftv
uid_of() { pm list packages -U "$1" 2>/dev/null | grep "package:$1 " | sed 's/.*uid://'; }
ORBIT_UID=$(uid_of home.jw.orbit)
AMAZON_UID=$(uid_of com.amazon.tv.launcher)
in_music=0          # 1 while you're in a music app because you opened it
video_since=0
last=0
had_video=0
echo 0 > $GEN

alexa_playing() { # Alexa's player has an active session that's playing (state=3)
  dumpsys media_session | sed -n "/package=$ALEXA_PLAYER/,/package=/p" | grep -E 'active=|state=PlaybackState' | grep -A1 'active=true' | grep -q 'state=PlaybackState {state=3,'
}

go_home_later() { # $1 = delay, $2 = generation it belongs to
  ( sleep "$1"
    if [ "$(cat $GEN)" = "$2" ] && dumpsys window windows | grep -q 'mCurrentFocus.*PassthroughPlayerActivity'; then
      am start -n $LAUNCHER >/dev/null 2>&1
    fi ) &
}

logcat -v brief -T 1 ActivityManager:I IPS-PassthroughPlayerTvInputCallback:I '*:S' 2>/dev/null |
while read -r line; do
  case "$line" in
    *"START u0"*"cmp=com.amazon.tv.launcher/.ui.HomeActivity_vNext"*)
      # Home fires two START lines ~200 ms apart; act once. The TV's shell does 32-bit arithmetic, so use only
      # the last 9 digits of the millisecond clock (it wraps every ~11.5 days: a negative gap counts as long ago).
      ms=$(date +%s%3N); now=$((10#${ms#????}))
      gap=$((now - last))
      if [ $gap -gt 1500 ] || [ $gap -lt 0 ]; then
        last=$now
        am start -n $LAUNCHER >/dev/null 2>&1 &
      fi ;;
    *"START u0"*"cmp=$ALEXA_PLAYER/"*)
      ( sleep 2
        if dumpsys window windows | grep -q "mCurrentFocus.*$ALEXA_PLAYER" && alexa_playing; then
          am start -n $LAUNCHER --ez idle true >/dev/null 2>&1
        fi ) & ;;
    *"START u0"*)
      app=${line##*cmp=}; app=${app%%/*}
      case " $MUSIC_APPS " in
        *" $app "*)
          from=${line##*from uid }; from=${from%% *}
          if [ -n "$ORBIT_UID" ] && { [ "$from" = "$ORBIT_UID" ] || [ "$from" = "$AMAZON_UID" ]; }; then
            in_music=1      # you opened it: stay
          elif [ $in_music = 0 ]; then
            ( sleep 2
              if dumpsys window windows | grep -q "mCurrentFocus.*$app"; then am start -n $LAUNCHER --ez idle true >/dev/null 2>&1; fi ) &
          fi ;;
        *) in_music=0 ;;
      esac ;;
    *"onVideoAvailable()"*)
      [ $had_video = 1 ] || video_since=$(date +%s)
      had_video=1
      echo $(( $(cat $GEN) + 1 )) > $GEN ;;
    *"onVideoUnavailable()"*)
      g=$(( $(cat $GEN) + 1 )); echo $g > $GEN
      if [ $had_video = 0 ]; then go_home_later $NEVER_DELAY $g
      elif [ $(( $(date +%s) - video_since )) -ge $SETTLE ]; then go_home_later $LOST_DELAY $g
      else go_home_later $UNSETTLED_DELAY $g; fi
      had_video=0 ;;
  esac
done
