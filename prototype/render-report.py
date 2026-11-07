"""Render the self-contained review artifact from existing measured evidence only."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
file = root / "results/observations.json"
report = json.loads(file.read_text())
for key, name in [("ios", "ios-observations.json"), ("iosBuild", "build-observations.json")]:
    source = root / "results" / name
    if source.exists():
        report[key] = json.loads(source.read_text())
file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
page = (root / "report-template.html").read_text().replace("__REPORT__", payload)
(root / "results/report.html").write_text(page)
