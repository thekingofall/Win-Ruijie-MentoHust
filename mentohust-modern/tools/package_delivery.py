"""Create source ZIP and dependency notices next to the built executable."""
from pathlib import Path
import importlib.metadata as metadata
import hashlib
import json
import shutil
import sys
import zipfile

root = Path(__file__).resolve().parents[2]
dist = root / "dist"
dist.mkdir(exist_ok=True)
licenses = dist / "licenses"
licenses.mkdir(exist_ok=True)
packages = ("pefile", "Pillow", "pystray", "scapy", "six", "pyinstaller")
lines = ["# Third-party software", "", "Installed distribution metadata and original license texts are retained below.",
         "Dependencies keep their respective licenses; the project GPL-3.0 file applies to the upstream application and its modifications.", "",
         "| Package | Version | License metadata |", "| --- | --- | --- |"]
for name in packages:
    distribution = metadata.distribution(name)
    license_name = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "See package source"
    lines.append(f"| {name} | {distribution.version} | {license_name} |")
    target = licenses / name
    target.mkdir(exist_ok=True)
    for i, item in enumerate(distribution.files or []):
        if "license" in str(item).lower() or str(item).endswith("COPYING.txt"):
            source = Path(distribution.locate_file(item))
            if source.is_file():
                shutil.copyfile(source, target / f"{i}-{source.name}")
    if name == "pystray":
        source = Path(distribution.locate_file("pystray/__init__.py"))
        (target / "source-license-header.txt").write_text(source.read_text(encoding="utf-8").split("import os")[0], encoding="utf-8")
        (target / "upstream.txt").write_text("https://github.com/moses-palmer/pystray/tree/v0.19.5\nLGPL v3 or later, according to the source headers.\n", encoding="utf-8")
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    shutil.copyfile(python_license, licenses / "Python-LICENSE.txt")
for item in (Path(sys.base_prefix) / "tcl").glob("*/license.terms"):
    shutil.copyfile(item, licenses / (item.parent.name + "-license.terms"))
(dist / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
lock = sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata.get("Name") != "mentohust-win-modern")
(root / "mentohust-modern" / "requirements-build.lock.txt").write_text("# Windows Python 3.12 build environment\n"+"\n".join(lock)+"\n", encoding="utf-8")
from mentohust_modern import APP_VERSION
source_zip = dist / f"新锐捷-{APP_VERSION}-source.zip"
with zipfile.ZipFile(source_zip, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
    for relative in ("LICENSE", "NOTICE.md", "README.md"):
        bundle.write(root / relative, relative)
    for folder in ("mentohust-modern/src", "mentohust-modern/tests", "mentohust-modern/tools"):
        for item in (root / folder).rglob("*"):
            if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
                bundle.write(item, item.relative_to(root))
    for relative in ("mentohust-modern/README.md", "mentohust-modern/pyproject.toml", "mentohust-modern/launcher.py", "mentohust-modern/requirements-build.lock.txt", "reports/gui-preview.png", "reports/release-validation.md"):
        bundle.write(root / relative, relative)
portable_zip = dist / f"XinRuijie-{APP_VERSION}-Windows-x64.zip"
with zipfile.ZipFile(portable_zip, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
    for relative in (f"新锐捷-{APP_VERSION}.exe", "THIRD_PARTY_NOTICES.md"):
        bundle.write(dist / relative, relative)
    for relative in ("LICENSE", "NOTICE.md", "README.md", "reports/gui-preview.png", "reports/release-validation.md"):
        bundle.write(root / relative, relative)
    for item in licenses.rglob("*"):
        if item.is_file():
            bundle.write(item, item.relative_to(dist))
checksums = {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in (dist / f"新锐捷-{APP_VERSION}.exe", source_zip, portable_zip)}
(dist / "SHA256SUMS.json").write_text(json.dumps(checksums, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
print(json.dumps(checksums, ensure_ascii=False))
