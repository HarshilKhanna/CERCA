"""
extract_dsb.py  — run once after curl.exe downloads DSB.zip
Extracts only socialNetwork/ from the archive into e:\CERCA\dsb\
Skips any entry whose path contains Windows-illegal characters (* ? : < > | ")
"""
import os
import pathlib
import sys
import zipfile

tmp  = os.path.join(os.environ["TEMP"], "DSB.zip")
dest = r"e:\CERCA\dsb"
ILLEGAL = set('*?:<>|"')

if not os.path.exists(tmp):
    print(f"ERROR: {tmp} not found. Run curl.exe download first.")
    sys.exit(1)

pathlib.Path(dest).mkdir(parents=True, exist_ok=True)

print(f"Opening {tmp} ...", flush=True)
with zipfile.ZipFile(tmp) as z:
    all_names = z.namelist()
    sn_entries = [n for n in all_names if "/socialNetwork/" in n]
    print(f"Found {len(sn_entries)} entries under socialNetwork/", flush=True)

    n_ok = n_skip = 0
    for name in sn_entries:
        # Strip leading archive root (e.g. "DeathStarBench-master/")
        rel = name.split("/", 1)[1]   # e.g. "socialNetwork/nginx-web-server/..."

        # Skip entries with Windows-illegal characters
        if any(c in rel for c in ILLEGAL):
            print(f"  SKIP (illegal char): {rel}")
            n_skip += 1
            continue

        target = os.path.join(dest, rel.replace("/", os.sep))

        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
            n_ok += 1

print(f"\nExtracted : {n_ok} files")
print(f"Skipped   : {n_skip} (illegal characters in path)")
print(f"Destination: {dest}")

os.remove(tmp)
print("Cleaned up DSB.zip from TEMP.")
