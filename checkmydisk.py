"""Check HDD and SSD health using smartctl."""

import argparse
import json
import subprocess
import sys


def read_smart(*arguments):
    result = subprocess.run(
        ["smartctl", "--json", *arguments],
        capture_output=True, text=True, errors="replace", timeout=30,
    )
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        detail = result.stderr.strip() or "smartctl did not return JSON"
        raise RuntimeError(detail) from error
    if not isinstance(data, dict) or result.returncode < 0:
        raise RuntimeError("smartctl did not return a usable report")
    return data, result.returncode


def assessment(data, status):
    failed = []
    warnings = []
    passed = data.get("smart_status", {}).get("passed")
    if passed is False or status & 0x08:
        failed.append("The drive reported a SMART health failure")
    if status & 0x10:
        failed.append("A SMART pre-failure attribute is at or below its threshold")
    for bit, message in (
        (0x20, "SMART attributes have reached a threshold"),
        (0x40, "The device error log contains errors"),
        (0x80, "The self-test log contains errors"),
    ):
        if status & bit:
            warnings.append(message)

    metrics = []
    ata_counts = {
        "Reallocated_Sector_Ct": "Reallocated sectors",
        "Current_Pending_Sector": "Pending sectors",
        "Offline_Uncorrectable": "Offline uncorrectable sectors",
    }
    for attribute in data.get("ata_smart_attributes", {}).get("table", []):
        label = ata_counts.get(attribute.get("name"))
        count = attribute.get("raw", {}).get("value")
        if label and count is not None:
            metrics.append((label, count))
            if count > 0:
                warnings.append(f"{label}: {count}")

    nvme = data.get("nvme_smart_health_information_log", {})
    used = nvme.get("percentage_used")
    if used is not None:
        metrics.append(("Estimated endurance used", f"{used}%"))
        if used >= 100:
            warnings.append("Estimated endurance used has reached 100%")
    media_errors = nvme.get("media_errors")
    if media_errors is not None:
        metrics.append(("Media and data integrity errors", media_errors))
        if media_errors > 0:
            warnings.append(f"Media and data integrity errors: {media_errors}")
    critical = nvme.get("critical_warning")
    if critical is not None:
        metrics.append(("Critical warning flags", f"0x{critical:02x}"))
    if critical:
        failed.append(f"NVMe critical warning flags: 0x{critical:02x}")

    incomplete = status & 0x07 or passed is None
    if incomplete:
        warnings.append("SMART health data is incomplete or unavailable")
    if failed:
        return "FAILED", 1, metrics, failed + warnings
    if incomplete:
        return "UNKNOWN", 2, metrics, warnings
    if warnings:
        return "WARNING", 1, metrics, warnings
    return "PASSED", 0, metrics, []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check HDD or SSD health using SMART data.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="list detected drives")
    action.add_argument("device", nargs="?", help="whole-drive path, such as /dev/sda")
    parser.add_argument("--type", metavar="TYPE", help="smartctl device type, such as sat for a USB bridge")
    args = parser.parse_args(argv)
    if args.type and not args.device:
        parser.error("--type requires a device")
    if args.device and args.device.startswith("-"):
        parser.error("supply a device path")

    try:
        if args.list:
            data, status = read_smart("--scan-open")
            if not status:
                for device in data.get("devices", []):
                    print(f"{device['name']}  ({device['type']})")
                if not data.get("devices"):
                    print("No drives detected.")
                return 0
        else:
            options = ["--device", args.type] if args.type else []
            data, status = read_smart("--all", *options, args.device)
    except FileNotFoundError:
        print("CheckMyDisk: install smartmontools and ensure smartctl is on PATH.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("CheckMyDisk: smartctl timed out.", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as error:
        print(f"CheckMyDisk: {error}", file=sys.stderr)
        return 2

    if status & 0x07 or args.list:
        for message in data.get("smartctl", {}).get("messages", []):
            print(f"smartctl: {message['string']}", file=sys.stderr)
    if args.list:
        print(f"CheckMyDisk: device scan failed (smartctl exit {status}).", file=sys.stderr)
        return 2

    verdict, exit_code, metrics, reasons = assessment(data, status)
    model = data.get("model_name") or data.get("scsi_model_name")
    print(f"Drive: {args.device}" + (f" ({model})" if model else ""))
    print(f"Health: {verdict}")
    temperature = data.get("temperature", {}).get("current")
    hours = data.get("power_on_time", {}).get("hours")
    if temperature is not None:
        print(f"Temperature: {temperature} °C")
    if hours is not None:
        print(f"Power-on hours: {hours}")
    for label, value in metrics:
        print(f"{label}: {value}")
    for reason in reasons:
        print(f"- {reason}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
