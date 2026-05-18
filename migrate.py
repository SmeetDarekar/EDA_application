# migrate.py
import json, os, glob

datadump = "datadump"

for fpath in glob.glob(f"{datadump}/**/*.json", recursive=True):
    if fpath.endswith("registry.json"):
        continue

    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    snap = data.get("abt_snapshot", {})
    fname = os.path.basename(fpath)
    ver = int(fname.replace("v", "").replace(".json", ""))
    table_name = os.path.basename(os.path.dirname(fpath))

    snap["version"] = ver
    snap["table_name"] = table_name
    snap["name"] = f"{table_name}_v{ver}"
    data["abt_snapshot"] = snap

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Fixed: {fpath}  →  name={snap['name']}, version={ver}")

print("Done. Restart Flask.")