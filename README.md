# Orbit-Helper

A small home-server companion for the [Orbit](https://github.com/jwizenfeld04/Orbit-Home) TV home screen. It does the things Fire OS doesn't let a normal app do, over ADB from a machine on your network:

- **Home button opens Orbit.** Fire TV always opens Amazon's home screen. The helper keeps a small watcher script, `homewatch.sh`, running on the TV that opens Orbit about 0.7 s after Home, and restarts it whenever the TV reboots.
- **HDMI input tiles.** Orbit's PlayStation / Switch / Xbox… tiles ask the helper, which switches the input. If a PS4 is on that port and in Rest Mode, it wakes the PS4 first.
- **Music jumps back to Orbit.** When Spotify or Amazon Music opens by itself (an Alexa voice request, a phone connecting with Spotify Connect), or Alexa's full-screen music player appears, the TV goes back to Orbit's idle mode after 2 s, with the song and lyrics showing. Opening those apps yourself is left alone.
- **Game line.** With [Sports Remote](https://github.com/jwizenfeld04/Sports-Remote) on the same network, Orbit shows tonight's games with live scores, and OK starts a game in the right app.
- **Back to Orbit when an HDMI device turns off.** When a console goes to sleep and its input loses the picture, the TV returns to Orbit instead of sitting on a black input.

It's one stdlib Python file plus one shell script, with no dependencies beyond Python 3.9+ and `adb`.

## What you need

- **An always-on machine on the same network as the TV:** a mini PC, a NAS that runs Docker or Python, or a Raspberry Pi. Linux is assumed (systemd); macOS works with your own way of starting it.
- **`adb`** (`sudo apt install adb`, or Android's platform-tools).
- **The TV** with ADB debugging on and a fixed IP (a DHCP reservation on your router). Built and tested on Fire TV (Fire OS 7). On Android TV / Google TV, Orbit switches inputs and owns the Home button by itself, so you'd only want the helper for the game line.
- **Optional:** [Sports Remote](https://github.com/jwizenfeld04/Sports-Remote) for the game line; a PS4 paired for Remote Play for the wake-up.

## What it can't do

- **Turn the TV on or off, or change the volume.** That's CEC, which the TV does itself.
- **Wake a Nintendo Switch or Xbox.** There's no network wake for them; only the PS4 is woken.
- **Hide Amazon's home screen completely.** It flashes for a moment before Orbit comes back.
- **Control more than one TV.** Run a second copy, with its own `.env` and port, for a second TV.
- **Work while the server is down.** The HDMI tiles show "Couldn't switch to …" and Home stays on Amazon's screen until the server is back.

## Install

```bash
git clone https://github.com/jwizenfeld04/Orbit-Helper.git ~/Orbit-Helper
cd ~/Orbit-Helper
cp .env.example .env && chmod 600 .env     # then set TV_IP (and anything else you use)
adb connect <tv-ip>:5555                   # approve the prompt on the TV; tick "Always allow"
mkdir -p ~/.config/systemd/user && cp orbit-helper.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now orbit-helper
sudo loginctl enable-linger "$USER"        # keep it running when you're logged out
```

The service file expects the clone at `~/Orbit-Helper`; edit `ExecStart` if it's elsewhere. Then, in Orbit, set Settings → "Home server (game line)" to `<server-ip>:8788`.

**Firewall:** allow the TV to reach port 8788 on the server, e.g. `sudo ufw allow from <tv-ip> to any port 8788 proto tcp`. For PS4 wake, also allow its replies, which come from a random port: `sudo ufw allow from <ps4-ip> proto udp`.

Check it: `journalctl --user -u orbit-helper -f` should say `listening on …, accepting only <tv-ip>`, and within 30 s that it started homewatch on the TV.

## Configuration (`.env`)

| Key | | |
|---|---|---|
| `TV_IP` | required | The TV. The helper controls it over ADB and refuses every other client. |
| `BIND`, `PORT` | optional | Address and port to listen on. Default `0.0.0.0:8788`. |
| `SPORTS_REMOTE_URL` | optional | Sports Remote's address. Default `http://127.0.0.1:8787`. |
| `SPORTS_REMOTE_PIN` | optional | Sports Remote's household PIN, if it uses one. The helper signs in with it, so the PIN never goes to the TV. |
| `LOCAL_TEAMS` | optional | Local teams as `league:ABBR`, comma-separated (e.g. `nfl:NYG,mlb:NYM`). Only used when Sports Remote doesn't report your market. |
| `TZ` | optional | Time zone for "Tonight" / "Today" on the game line. Default: this machine's. |
| `PS4_HDMI` | optional | The HDMI port the PS4 is on. Default `3`. |
| `PS4_HOST`, `PS4_CREDENTIAL` | optional | The PS4's IP and its Remote Play credential, for wake-up. Leave empty if you have no PS4. |
| `HOMEWATCH` | optional | `0` skips pushing `homewatch.sh` to the TV (Android TV / Google TV, where Orbit owns Home itself). Default `1`. |

`.env` is gitignored; keep it at mode 600.

**Sports Remote:** the game line works once Sports Remote is set up (household, services, TVs) and the helper can sign in: with PIN sign-in, put the PIN in `SPORTS_REMOTE_PIN`; the helper then calls `/api/bootstrap` for the games and `/api/launch` to start one. Favorites and local teams come from Sports Remote's settings. Without a PIN the game line stays empty (Orbit's setup screen reports "Sports Remote PIN missing on the server").

**PS4 wake-up:** the PS4 needs Settings → Remote Play enabled, and Power Save Settings → Features Available in Rest Mode → "Stay Connected to the Internet" and "Enable Turning On PS4 from Network". Pair once with [pyremoteplay](https://github.com/ktnrg45/pyremoteplay): sign in to PSN, register with the PIN from Settings → Remote Play Connection Settings → Add Device, and put `format_regist_key(<key>)` in `PS4_CREDENTIAL`. Give the PS4 a fixed IP too.

## API

Requests are only accepted from `TV_IP`.

| Route | |
|---|---|
| `POST /hdmi/<1-4>` | Switch to that HDMI input (waking the PS4 first if it's on `PS4_HDMI`). |
| `GET /glance?leagues=nfl,mlb,nhl,nba` | The games Orbit's game line should show, from Sports Remote. |
| `POST /glance/launch` `{"gameId": …}` | Start that game on the TV through Sports Remote. |

## homewatch.sh

Runs on the TV as the ADB shell user, which, unlike an app, Fire OS lets open apps instantly after Home. It reads Android's activity log and:
- opens Orbit (`LAUNCHER=` at the top of the script) whenever Amazon's home screen starts;
- returns to Orbit when an HDMI input loses its picture: after 2 s if the picture had been stable, 10 s if it had only just appeared, and 60 s if it never showed one;
- sends Spotify, Amazon Music or Alexa's music screen to Orbit's idle mode when they open by themselves.

The helper pushes the copy in this folder to the TV and restarts it within 30 s if it isn't running (after a TV reboot, or after `adb shell pkill -f homewatch.sh` to load a new version).

It is written for Fire OS 7 and keys on Amazon's component names, set at the top of the script: Amazon's home screen (`com.amazon.tv.launcher/.ui.HomeActivity_vNext`), the HDMI passthrough player (`PassthroughPlayerActivity` and its `IPS-PassthroughPlayerTvInputCallback` log tag), Alexa's player (`ALEXA_PLAYER`) and the music apps (`MUSIC_APPS`). A different Fire OS version may name these differently (`adb logcat -v brief ActivityManager:I '*:S'` while pressing Home shows the current one). On Android TV / Google TV it does nothing useful; set `HOMEWATCH=0`.

## Changes and releases

Every change goes through a pull request with a `CHANGELOG.md` entry. See [RELEASING.md](RELEASING.md). MIT licensed.
