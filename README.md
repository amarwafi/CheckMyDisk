# CheckMyDisk

CheckMyDisk reads SMART diagnostics to evaluate HDDs and SSDs, including SATA and
NVMe drives. It reads the drive's existing health data and logs without changing
drive settings or starting self-tests.

## Desktop app on Linux

Install Python 3 and smartmontools. Create a virtual environment, install the
Qt Widgets dependency, and launch CheckMyDisk:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-gui.txt
.venv/bin/python checkmydisk_gui.py
```

Select a drive to read its SMART data. **Refresh** checks the selected drive
again. **Rescan drives** looks for drives that have been connected or removed.
Select **Authorize all drives** to check every listed drive with one desktop
authentication prompt. Select a drive afterward to read its saved result.
Use **Authorize all drives** again to repeat the elevated batch. This requires PolicyKit
(`pkexec` and a desktop authentication agent) and a system installation of
`smartctl`. Your desktop app continues running as your current user.

## Command line on Linux

Install Python 3 and [smartmontools](https://www.smartmontools.org/), then run:

```sh
sudo python3 checkmydisk.py --list
sudo python3 checkmydisk.py /dev/sda
sudo python3 checkmydisk.py /dev/nvme0
```

Use the whole-drive path printed by `--list`. Omit `sudo` if your account can
already read SMART data. If your USB enclosure needs a device type, pass the
type reported by the scan, for example `--type sat`. Some enclosures and
controllers do not expose SMART data.

## Read the result

| Result | Meaning | Exit code |
| --- | --- | --- |
| PASSED | SMART passed and the available indicators showed no warnings | 0 |
| WARNING | SMART passed but counters or logs show issues worth checking | 1 |
| FAILED | The drive reported failing health, a pre-failure threshold, or an NVMe critical warning | 1 |
| UNKNOWN | The tool could not establish health from the available data | 2 |

CheckMyDisk shows temperature, power-on hours, and supported sector or endurance
counters when the drive reports them. SATA attribute names and meanings can vary
by vendor. A passing result reflects the diagnostics available at the time of
the check.
