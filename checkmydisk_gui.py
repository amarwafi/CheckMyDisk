"""Desktop interface for CheckMyDisk."""

import json
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from checkmydisk import assessment, read_smart


def messages(data):
    return [message["string"] for message in data.get("smartctl", {}).get("messages", [])]


def list_drives():
    data, status = read_smart("--scan")
    if status:
        raise RuntimeError("\n".join(messages(data)) or f"Drive scan failed (smartctl exit {status}).")
    return data.get("devices", [])


def inspect_drive(drive):
    options = ["--device", drive["type"]] if drive.get("type") else []
    return make_report(*read_smart("--all", *options, drive["name"]))


def inspect_all_authorized(drives):
    pkexec = Path("/usr/bin/pkexec")
    smartctl = next((path for path in ("/usr/sbin/smartctl", "/usr/bin/smartctl")
                     if Path(path).is_file()), None)
    if not pkexec.is_file() or smartctl is None:
        raise RuntimeError("Authorization requires pkexec and a system installation of smartctl.")

    arguments = []
    for drive in drives:
        if not drive["name"].startswith("/dev/"):
            raise RuntimeError("Authorization requires Linux device paths.")
        arguments.extend((drive["name"], drive.get("type") or "auto"))

    # Positional arguments are passed as data. The elevated command only reads SMART.
    script = """
smartctl=$1
shift
while [ "$#" -ge 2 ]; do
    report=$("$smartctl" --json=c --all --device="$2" "$1" 2>/dev/null)
    status=$?
    printf '%s %s\n' "$status" "$report"
    shift 2
done
"""
    result = subprocess.run(
        [str(pkexec), "--disable-internal-agent", "/bin/sh", "-c", script,
         "checkmydisk", smartctl, *arguments],
        capture_output=True, text=True, errors="replace", timeout=60 + 30 * len(drives),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Authorization failed or was canceled.")
    lines = result.stdout.splitlines()
    if len(lines) != len(drives):
        raise RuntimeError("The authorized SMART check returned an incomplete batch.")
    reports = []
    for line in lines:
        status, _, payload = line.partition(" ")
        try:
            reports.append(make_report(json.loads(payload), int(status)))
        except (ValueError, TypeError, AttributeError):
            reports.append(({}, "UNKNOWN", [], ["smartctl returned no usable report for this drive."]))
    return reports


def make_report(data, status):
    verdict, _, metrics, reasons = assessment(data, status)
    if status & 0x07:
        reasons += messages(data)
    return data, verdict, metrics, reasons


class JobSignals(QObject):
    completed = Signal(object, str)


class DriveJob(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = JobSignals()

    def run(self):
        try:
            result = self.operation()
        except FileNotFoundError:
            self.signals.completed.emit(None, "Install smartmontools and make sure smartctl is on PATH.")
        except subprocess.TimeoutExpired:
            self.signals.completed.emit(None, "smartctl timed out.")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            self.signals.completed.emit(None, str(error))
        else:
            self.signals.completed.emit(result, "")


STYLE = """
QWidget#window { background: #ffffff; color: #18243a; }
QFrame#toolbar, QFrame#sidebar { background: #f4f7fc; }
QFrame#toolbar { border-bottom: 1px solid #dce4ef; }
QFrame#sidebar { border-right: 1px solid #dce4ef; }
QLabel { color: #18243a; }
QLabel[muted="true"] { color: #506079; }
QLabel[role="brand"], QLabel[role="section"] { font-weight: 700; }
QLabel[role="title"] { font-size: 30px; font-weight: 700; }
QLabel[role="metric"] { font-size: 21px; font-weight: 600; }
QPushButton { color: #18243a; background: #ffffff; border: 1px solid #b8c7dc;
              border-radius: 7px; padding: 6px 13px; }
QPushButton:hover { background: #e4ecfb; }
QPushButton:focus { border-color: #2855a2; }
QPushButton:disabled { color: #596579; background: #eaf0f8; }
QListWidget { background: transparent; border: none; color: #18243a; outline: 0; }
QListWidget::item { border-radius: 8px; padding: 8px; }
QListWidget::item:selected { background: #e4ecfb; color: #18243a; }
QListWidget::item:focus { border: 1px solid #2855a2; }
QFrame#health { border-radius: 10px; background: #eaf0f8; }
QTableWidget { background: #ffffff; color: #18243a; border: none;
               gridline-color: #dce4ef; }
QHeaderView::section { background: #f4f7fc; color: #506079; border: none; padding: 7px; }
"""


def label(text="", role="", muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setProperty("role", role)
    widget.setProperty("muted", muted)
    return widget


class DriveWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle("CheckMyDisk")
        self.setStyleSheet(STYLE)
        self.resize(860, 550)
        self.setMinimumSize(670, 470)
        self.pool = QThreadPool.globalInstance()
        self.job = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QFrame(objectName="toolbar")
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(20, 12, 20, 12)
        top.addWidget(label("CheckMyDisk", "brand"))
        top.addStretch()
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.refresh_report)
        top.addWidget(self.refresh)
        self.authorize = QPushButton("Authorize all drives")
        self.authorize.clicked.connect(self.authorize_all)
        self.authorized_reports = {}
        self.authorize.setEnabled(False)
        top.addWidget(self.authorize)
        outer.addWidget(toolbar)

        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(205)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 20, 12, 16)
        side.addWidget(label("Drives", "section"))
        self.drives = QListWidget()
        self.drives.setAccessibleName("Drives")
        self.drives.currentItemChanged.connect(self.select_drive)
        side.addWidget(self.drives)
        self.rescan = QPushButton("Rescan drives")
        self.rescan.clicked.connect(self.scan)
        side.addWidget(self.rescan)
        body.addWidget(sidebar)

        report = QVBoxLayout()
        report.setContentsMargins(28, 22, 28, 22)
        report.setSpacing(10)
        body.addLayout(report, 1)
        self.title = label("Drive health", "title")
        self.title.setWordWrap(True)
        self.device = label(muted=True)
        report.addWidget(self.title)
        report.addWidget(self.device)

        self.health = QFrame(objectName="health")
        health_layout = QVBoxLayout(self.health)
        health_layout.setContentsMargins(15, 11, 15, 11)
        self.verdict = label(role="section")
        self.reason = label()
        self.reason.setWordWrap(True)
        health_layout.addWidget(self.verdict)
        health_layout.addWidget(self.reason)
        report.addWidget(self.health)

        readings = QGridLayout()
        readings.addWidget(label("Temperature", muted=True), 0, 0)
        readings.addWidget(label("Power-on hours", muted=True), 0, 1)
        self.temperature = label(role="metric")
        self.hours = label(role="metric")
        readings.addWidget(self.temperature, 1, 0)
        readings.addWidget(self.hours, 1, 1)
        report.addLayout(readings)
        report.addWidget(label("SMART indicators", "section"))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Indicator", "Reported value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        report.addWidget(self.table, 1)
        self.scan()

    def run_job(self, operation, callback):
        self.drives.setEnabled(False)
        self.refresh.setEnabled(False)
        self.authorize.setEnabled(False)
        self.rescan.setEnabled(False)
        self.job = DriveJob(operation)
        self.job.signals.completed.connect(callback, Qt.ConnectionType.QueuedConnection)
        self.pool.start(self.job)

    def finish_job(self):
        self.job = None
        self.drives.setEnabled(True)
        self.refresh.setEnabled(self.drives.currentItem() is not None)
        self.rescan.setEnabled(True)
        self.authorize.setEnabled(self.drives.count() > 0)

    def scan(self):
        self.authorized_reports.clear()
        current = self.drives.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.previous_drive = selected
        self.display("Drive health", "", "SCANNING", "Looking for connected drives.")
        self.run_job(list_drives, self.show_drives)

    def show_drives(self, drives, error):
        self.drives.blockSignals(True)
        self.drives.clear()
        selected_row = 0
        for row, drive in enumerate(drives or []):
            item = QListWidgetItem(f"{drive['name']}\n{drive.get('protocol', drive.get('type', 'Drive'))}")
            item.setData(Qt.ItemDataRole.UserRole, drive)
            self.drives.addItem(item)
            if drive == self.previous_drive:
                selected_row = row
        if self.drives.count():
            self.drives.setCurrentRow(selected_row)
        self.drives.blockSignals(False)
        self.finish_job()
        if error:
            self.display("Drive health", "", "UNKNOWN", error)
        elif not drives:
            self.display("Drive health", "", "NO DRIVES FOUND", "Connect a drive and select Rescan drives.")
        else:
            self.refresh_report()

    def select_drive(self, current, _previous):
        if current and self.job is None:
            drive = current.data(Qt.ItemDataRole.UserRole)
            cached = self.authorized_reports.get((drive["name"], drive.get("type")))
            if cached is None:
                self.refresh_report()
            else:
                self.show_report(cached, "")

    def authorize_all(self):
        items = [self.drives.item(index) for index in range(self.drives.count())]
        drives = [item.data(Qt.ItemDataRole.UserRole) for item in items]
        self.display("Drive health", "", "CHECKING", "Authorizing and reading SMART data for all listed drives.")
        self.run_job(lambda: inspect_all_authorized(drives), self.show_authorized_reports)

    def show_authorized_reports(self, reports, error):
        if error:
            self.show_report(None, error)
            return
        for index, report in enumerate(reports):
            item = self.drives.item(index)
            drive = item.data(Qt.ItemDataRole.UserRole)
            self.authorized_reports[(drive["name"], drive.get("type"))] = report
            self.update_drive_item(item, report)
        drive = self.drives.currentItem().data(Qt.ItemDataRole.UserRole)
        self.show_report(self.authorized_reports[(drive["name"], drive.get("type"))], "")

    def refresh_report(self):
        item = self.drives.currentItem()
        if item is None or self.job is not None:
            return
        drive = item.data(Qt.ItemDataRole.UserRole)
        self.display(drive["name"], "", "CHECKING", "Reading SMART diagnostics.")
        self.authorized_reports.pop((drive["name"], drive.get("type")), None)
        self.run_job(lambda: inspect_drive(drive), self.show_report)

    def show_report(self, report, error):
        self.finish_job()
        item = self.drives.currentItem()
        drive = item.data(Qt.ItemDataRole.UserRole)
        path = drive["name"]
        if error:
            self.display(path, "", "UNKNOWN", error)
            return
        data, verdict, metrics, reasons = report
        model = data.get("model_name") or data.get("scsi_model_name") or path
        self.update_drive_item(item, report)
        device = data.get("device", {})
        protocol = device.get("protocol") or drive.get("protocol") or drive.get("type")
        capacity = data.get("user_capacity", {}).get("bytes")
        size = f"{capacity / 1e12:g} TB" if capacity and capacity >= 1e12 else (
            f"{capacity / 1e9:g} GB" if capacity else ""
        )
        details = " · ".join(part for part in (size, protocol, path) if part and part != model)
        temperature = data.get("temperature", {}).get("current")
        hours = data.get("power_on_time", {}).get("hours")
        reason = "\n".join(reasons) if reasons else "No SMART warnings reported."
        self.display(model, details, verdict, reason, temperature, hours, metrics)

    def update_drive_item(self, item, report):
        drive = item.data(Qt.ItemDataRole.UserRole)
        data, verdict, _, _ = report
        path = drive["name"]
        model = data.get("model_name") or data.get("scsi_model_name")
        title = f"{model}\n{path}" if model and model != path else path
        item.setText(f"{title} · {verdict.title()}")

    def display(self, name, device, verdict, reason, temperature=None, hours=None, metrics=()):
        self.title.setText(name)
        self.device.setText(device)
        self.device.setVisible(bool(device))
        self.verdict.setText("SMART passed" if verdict == "PASSED" else verdict.capitalize())
        self.reason.setText(reason)
        fill, ink = {
            "PASSED": ("#e7f6ee", "#11664a"),
            "WARNING": ("#fff1d8", "#825000"),
            "FAILED": ("#fdebea", "#9b2923"),
        }.get(verdict, ("#eaf0f8", "#394964"))
        self.health.setStyleSheet(f"QFrame#health {{ background: {fill}; }} QLabel {{ color: {ink}; }}")
        self.temperature.setText("Unavailable" if temperature is None else f"{temperature} °C")
        self.hours.setText("Unavailable" if hours is None else f"{hours:,} h")
        rows = metrics or [("No indicators available", "—")]
        self.table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            cell = QTableWidgetItem(str(value))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 1, cell)


def main():
    app = QApplication(sys.argv)
    window = DriveWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
