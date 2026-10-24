# bp1pro-linux

Unofficial Linux control for **Baseus Bass BP1 Pro** earbuds, over the
classic-Bluetooth **SPP control protocol** that the official Baseus Home app
(`com.baseus.intelligent` v2.17.0.1) speaks. The protocol was reverse
engineered from the app's decompiled sources — every frame in this README is
sourced to `file:line`. Nothing proprietary is bundled: pure Python standard
library, and it talks to your buds with the same user-mode commands their
own app sends.

Pair your buds normally; **audio always flows over the standard A2DP path**
(any player, any desktop, PipeWire or PulseAudio as usual). This project adds
the part Linux was missing: the vendor switches.

**What it controls:** ANC (mode + strength), EQ presets with editable names,
8D / spatial sound (music / cinema), in-ear detect, low-latency game mode,
gesture bindings, live battery for left bud / right bud / charging case.

## Install

```sh
git clone https://github.com/dream-creator/bp1pro-linux.git
cd bp1pro-linux
./install-desktop.sh       # per-user, no sudo
```

`install-desktop.sh` symlinks the CLI (`~/.local/bin/bp1pro`) and the GUI
launchers, installs the icon and the app-grid entry (the `Exec` path in
`desktop/bp1pro.desktop.template` is rewritten to your `$HOME`). Then open
**Activities** (Super key) and type *"BP1"*.

The window has tabs for Device / Noise control / EQ / **8D & spatial** /
Gestures / Log, remembers the earbud MAC in `~/.config/bp1pro.json`, and:

* connects on app start (keeping the buds' audio playing over A2DP),
* repairs a soft-blocked / powered-off adapter (`rfkill unblock` +
  `bluetoothctl power on` — runs unprivileged),
* shows every error in a red banner at the top (not buried in the Log tab),
* queues the button you pressed and fires it the moment the link is up,
* sweeps battery + ANC + 8D state after connecting and mirrors the values
  on screen,
* keeps a persistent battery row (left / right / charging case) on the
  Device tab — the buds push `AA02`/`AA27` **unsolicited ~1.5 s after the
  SPP link opens, between exchanges**, and answer `BA33` with ANC only; a
  quiet status poll every 4 s drains whatever they queued, so the row
  fills within seconds of connecting and drifts with the buds live,
* gives EQ presets editable names (they are cloud config inside the
  official app; here you label them yourself, saved in
  `~/.config/bp1pro.json`).

Window sketch (the Log tab fills with live `TX`/`RX` lines like these):


```
┌──────────────────────────────────────────────────────────────────┐
│ bp1pro AA:BB:CC:DD:EE:FF ch3 | ANC status: noise cancelling      │
├──────────────────────────────────────────────────────────────────┤
│  status - query ANC / in-ear / audio-mode state                  │
│  ANC: set mode (off / noise cancelling / transparency)           │
│  ANC: adjust strength level                                      │
│  EQ: select preset (Enter lists names)                           │
│  8D: set spatial mode (off/music/cinema)                         │
│  In-ear detect: on/off                                           │
│  Low-latency game mode: on/off                                   │
│  Gesture: bind function to click                                 │
│  Gesture: read button config                                     │
│  Send raw hex frame                                              │
│  Reconnect SPP channel                                           │
├──────────────────────────────────────────────────────────────────┤
│ TX BA340128                                                      │
│ RX AA3401   (ANC ack: ok)                                        │
└──────────────────────────────────────────────────────────────────┘
```

## Requirements

- Linux with BlueZ (`bluetoothctl` on PATH — every mainstream distro)
- Python 3.10+ — **standard library only** for the CLI/TUI/library (no pip
  packages); the GTK3 desktop app additionally needs `python3-gi`, which
  GNOME systems have preinstalled (`apt install python3-gi` otherwise)
- Earbuds paired once with the machine; music keeps flowing over the normal
  A2DP path (PipeWire/PulseAudio controls volume); this tool talks on the
  SPP link the buds expose alongside the audio.

## Run

Straight from the tree, nothing to install:

```sh
python3 -m bp1pro --help          # CLI: every command has --help
python3 -m bp1pro tui             # terminal UI
python3 bp1pro_gui.py             # GTK app (needs python3-gi)
```

Demo mode with zero hardware: `python3 -m bp1pro --mock status`

## Find and pair

```sh
python3 -m bp1pro pair          # walkthrough + lists already-paired Baseus MACs
bluetoothctl pair <MAC> && bluetoothctl trust <MAC> && bluetoothctl connect <MAC>
python3 -m bp1pro status <MAC>  # verify the control link works
```

The earbuds must be switched on and connected (led blinking / audio active) —
the control socket rides the same ACL link. If they are asleep, the tool
attempt a `bluetoothctl connect` first, and tells you to take them out of the
case otherwise.

## Command line

Global: `-a/--address MAC` (or `$BP1PRO_ADDR`), `--channel N` (force SPP
channel; default probes the proven list 1, 2, 3, 8 with a `BA33` ping),
`--mock` (fake earbud), `-v`.

```sh
export BP1PRO_ADDR=AA:BB:CC:DD:EE:FF

bp1pro status                # ANC / in-ear / audio-mode / 8D query
bp1pro anc                   # read ANC state
bp1pro anc nc                # noise cancelling        (BA3401FF)
bp1pro anc nc --level 40     # cancelling + strength   (BA340128)
bp1pro anc transparency      # transparency mode
bp1pro anc off               # normal (noise off)
bp1pro eq                    # list EQ presets + your local names
bp1pro eq 0x02               # EQ preset 2             (BA3102)
bp1pro eq-name 2 "Bass boost" # label a preset locally (~/.config/bp1pro.json)
bp1pro spatial               # 8D / spatial state      (BA42 -> AA42)
bp1pro spatial cinema        # 8D mode                 (BA4302)
bp1pro inear on              # in-ear detect auto-pause on
bp1pro mode on               # low-latency audio mode  (BA2401)
bp1pro gesture double left 3 # double-click(L) = next track (BA2203 03 FF)
bp1pro gesture single both 6 # single-click(L+R) = ANC cycle (BA2201 06 06)
bp1pro gesture-q 0           # read button 0 config    (BA21 00)
bp1pro send BA39             # raw hex frame at the protocol level
bp1pro monitor               # poll & print incoming frames (^C to stop)
bp1pro tui                   # the terminal UI above
bp1pro --mock status         # demo without hardware
```

Gesture click set: `single` / `double` / `triple` / `long`; side:
`left` / `right` / `both`; function ids below (any decimal or 0xN).

| id | function            | id | function            |
|----|---------------------|----|---------------------|
| 0  | none                | 9  | rapid mode          |
| 1  | play / pause        | 10 | light effect toggle |
| 2  | previous track      | 11 | volume up           |
| 3  | next track          | 12 | volume down         |
| 4  | voice assistant     | 13 | bass boost          |
| 5  | low latency (game)  | 14 | sleep mode          |
| 6  | ANC mode toggle     | 16 | dynamic sound       |
| 7  | spatial sound       | 18 | take photo          |
| 8  | spatial (game only) | 27 | switch connection   |
|    |                     | 28 | mic on/off          |

## TUI

```sh
bp1pro tui
```

Menu with all actions, live TX/RX log pane, prompts for extra values
(ANC strength, EQ id, gesture binding). Keys: `up/down/j/k` move, `Enter`
run, `q` quit. Runs in any terminal, including over ssh; needs a real TTY
(not a pipe).

## The protocol (evidence)

Send frames start `BA`, replies start `AA`. Hex strings, upper case, no CRC
on this model family. Extracted from the app's decompiled sources
(jadx dump of `com.baseus.intelligent` v2.17.0.1):

| action            | frame                | evidence (file:line)                              |
|-------------------|----------------------|---------------------------------------------------|
| transport         | RFCOMM/SPP `00001101` **client** connect on channel **1** (live-verified 2026-10-24) | app is a client: `ClassicBtConnectManager.java:163`, `SppConnector.java:73/82` use `createRfcommSocketToServiceRecord`; no `listenUsingRfcomm*` anywhere in the app |
| ANC query         | `BA33` → `AA33 <mode> <level>` | `NoiseReduceDataModel.java:909`; reply layout `NoiseReducePopWindow.T()` (substring offsets 4-6/6-8) |
| ANC set           | `BA34 <mode> <level|FF>` → `AA34 01` ok / `AA34 0C 0D 00` fail reasons | `NoiseReduceDataModel.java:930`; ack handling + error toasts `NoiseReducePopWindow.java:585, 871-942` |
| EQ select         | `BA31 <id>` → `AA30 <id>` | `EarEqDefaultRegulationPresenter.java:346,369`; reply `startsWith("AA30")` |
| EQ preset names   | *not on the wire* — cloud config (`EqSoundModeBean {dictSort,label}`); this repo keeps a local editable map in `~/.config/bp1pro.json` (key `eq_names`) | `EarEqDefaultRegulationPresenter.f():105-110`; no catalog JSON in the APK assets (grep-verified) |
| case battery      | `AA27 <q>` — **push only, no query frame exists in the app** | `HomeBleDataResolvePresenter.c0():1045-1053` reads `substring(4,6)`; `EarphoneFunctionShowFragmentNewUI.t()` — `AA276400` = case 100% |
| battery push      | `AA02 <L> 00 <R> 01` (earbud battery left/right) | `BleUtils.d()` regex `AA02([xx])00([xx])01` — verified live: `AA0264006401` = 100/100; raw-socket evidence: pushed **unsolicited ~1.5 s after SPP link opens** (`AA02`,`AA27`,`AA3001` burst), not in reply to any query |
| 8D query          | `BA42` → `AA42 <mode>` | `PanoramicSoundViewModel.java:104` sends; `x():229` parses `substring(4,6)` |
| 8D set            | `BA43 <mode>` → `AA43 01` applied; failure codes `0A` (LHDC active), `0B` (LDAC active), `0C` (double-connect required), `0D` (in a call); `00`/`02`= buds answered current mode | `PanoramicSoundViewModel.java:126-134` sends ("BA43"+model); `EarphoneFunctionShowFragmentNewUI.java:628-711`, `EarEqDefaultRegulationActivityNewUi.java:990-1017` ack/failure |
| 8D modes          | `00` off/common · `01` music · `02` cinema = BP1 Pro set; `03` game = Bowie-family only; `04` fixed, `05` tracking = Bowie 30/35 Max + H1s Pro | mode table `SpatialSoundManger.java:96-111` (mode ids hard-equal wire bytes); gating `DeviceManager.h0/c1/d1/g0():1440-1565` + `PanoramicSoundViewModel.A()` — all false for "Baseus Bass BP1 Pro" |
| sound settings    | `BA23` query → `AA23 <val>` | `EarSoundSettingViewModel.java:246` (EarSoundResultBean) |
| gesture query     | `BA21 <btn>` | `GestureBleManager.d()` |
| gesture set       | `BA22 <op> <fL> <fR>`; op `01`=single `03`=double `00`=triple `04`=long; `FF` = leave unchanged | `GestureBleManager.f()` lines 53-139 (op table from the switch cases; `qdqqbpd.pdqppqb` = `"01"`) |
| in-ear detect     | `BA25` query / `BA26 <01|00>` set | `GestureBleManager.a()/b()` |
| audio/low-latency | `BA23` query / `BA24 <01|00>` set | `GestureBleManager.e()/g()`; `EarphoneFunctionShowFragmentNewUI.j()` (flag ? "01" : "00") |
| wear notify       | `AA3501` worn / `AA3500` off (unsolicited) | `NoiseReducePopWindow.java:934-948` |

The gesture *function* byte is simply the app function id, `"%02X"`-formatted
(`GestureFunctionChooseViewModel.u()`), names resolved by
`GestureDataResolveManager.g()` (`anc_mode_switch_tit`, `panoramic_sound`, …).

Mode bytes for ANC: `0` normal, `1` noise cancelling, `2` transparency —
matching `GestureDataResolveManager.j()` UI titles.

The ANC `level` byte carries **two value spaces** (keywords:
`NoiseReducePopWindow.java:980-1001` + `NoiseReduceViewModel.r()`):

- `0..100` — manual strength, echoed back verbatim by the firmware.
  Live-verified 2026-10-24 (`tests/probe_anc_sweep.py`): the buds accepted
  and echoed `00 19 32 63 64` unchanged, audibly stepping 0→25→50→99→100.
- `101..103` — named ANC presets (string resources `noise_commute`,
  `noise_indoor`, `noise_outdoor`), **not strengths**. `TypeBean`
  `(title, 101/102/103)`; custom entry is `(str_custorm, 1)`.
- `FF` — unset/auto → the app shows the first preset (`PopWindow:981`).

So a reply like `AA330165` means "preset 1 active", *not* strength 101 —
the official app never sends `100` as strength (`<100` branch at
`NoiseReducePopWindow.java:983`) because it reserves bytes ≥100 for the
preset-id space; the firmware itself is happy with the full 0-100 range.

### Live confirmation (2026-10-24)

Once the phone freed the SPP session, channel 1 answered instantly:

```
TX BA33
RX AA330165
   battery: left 100, right 100
   charging-case battery 100
   EQ ack: preset 0x01 selected
   sound settings reply: 0x00
```

### The single-session rule (important!)

The SPP control channel allows **exactly one client** (phone or this host):

* if a phone with the Baseus app (and Bluetooth on) is nearby, it holds the
  session and every connect attempt here is refused (`ECONNREFUSED`);
* the fix: turn the phone's Bluetooth off briefly while connecting from the
  PC — after our session exists, turning the phone back on is fine;
* the buds also ignore SDP service-record requests while their SPP server is
  busy, so `--channel` exists as the escape hatch (1 works, verified).

### Adapter rfkill quirk

acer-wireless laptops occasionally rfkill-soft-block the Bluetooth adapter by
themselves (kernel log shows `rfkill: input handler …` toggles). Every
connect in this tool first runs a best-effort unblock + power-on, so the app
survives that — the Log tab notes the repair.

### Unknown / extra frames (battery %, custom EQ bands, spatial audio…)

Anything the app sends beyond this set can be discovered with an Android
Bluetooth HCI snoop log: Developer settings enable the capture, use the app's
wanted feature, then read `/sdcard/btsnoop_hci.log` and look for RFCOMM frame
payloads (`BA…` writes, `AA…` reads for this family). Feed found frames
through `bp1pro send <hex>` and see what the buds say; the log pane records
everything.

## Verifying the RFCOMM channel

Connect probes the small proven-channel list (1, 2, 3, 8) with a `BA33` ping
and keeps the first that answers `AA33`. The buds' own SDP server stays
silent when their SPP server is busy, so an SDP detour was written and kept
as diagnostics:

```sh
python3 -m bp1pro.sdp <MAC>                 # SDP service-record probe
python3 tests/probe_channels_live.py <MAC>  # raw channel sweep (1..30)
python3 tests/probe_patient.py <MAC>        # slow negotiation probe
python3 tests/probe_spp_profile.py <MAC> [client|server]  # via bluetoothd
```

**BES BP1 Pro answers on channel 1** — verified live (link + BA33 → AA33).

## Tests

```sh
python3 -m unittest discover -s tests -v     # codec + transport (mocked) tests
python3 tests/tui_pty_smoke.py               # drives the TUI through a real pty
DISPLAY=:1 python3 tests/gui_smoke.py        # GTK GUI behaviour on the desktop
```

No hardware or BlueZ access needed for the test suite; the pty smoke test
exercises the full menu loop including prompts.

## Project layout

```
bp1pro/
  protocol.py    frame builders + reply parser/decoder (pure, no I/O)
  transport.py   RFCOMM transport: channel scan, exchanges, mock transport
  device.py      bluetoothctl wrappers (pair/connect/status/ensure_connected)
  cli.py         argument-driven commands
  tui.py         curses terminal UI
  gui.py         GTK3 desktop app (bp1pro-gui / app-grid entry)
  __main__.py    python -m bp1pro
tests/           unittest suites + pty smoke
```

## Caveats

- Skill level 0x255 (`FF`) means "unchanged" for optional parameters, per the
  app's placeholder convention.
- `AA34 0C` diagnosis: buds allow ANC config only when **Ambient** ANC
  (double-connected) is engaged, `0D` = blocked during a call, `00` = wear the
  buds properly and retry.
- Custom EQ band parameters (`BA31` with extra payload) have not been byte
  verified — HCI capture welcome.
- Battery: earbud % (left/right) and case % are decoded (AA02/AA27 above).
- The buds drop off the air when put back in the charging case
  (`EHOSTUNREACH` from this tool means exactly that). Reconnect after taking
  them out again.

## Contributing

Issues and PRs welcome. For new protocol frames the ideal diff includes the
frame itself plus where it came from — either `file:line` in an official app
build (jadx) or an HCI snoop excerpt (capture method in the *Unknown frames*
section above).

## License

[MIT](LICENSE) — free to use, modify, and redistribute; attribution
appreciated.

## Legal note

Independent community project — **not affiliated with, sponsored by, or
endorsed by Baseus.** "Baseus", "Bass", and "Bowie" are trademarks of their
respective owners and appear here only to identify the hardware this tool
controls. No Baseus code or assets are included: everything in this
repository was written from scratch; the protocol table merely *points to*
file/line references in the official app build (obtained by decompiling an
app the repo author had installed) to document how each frame was derived.
Use at your own risk — the buds respond to any control client the same way,
and this tool sends nothing beyond the frames their own app sends.
