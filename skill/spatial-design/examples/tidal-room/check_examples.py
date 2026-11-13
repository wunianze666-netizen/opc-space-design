"""Run the public examples with the default renderer and check the handoff."""

import csv
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory


EXAMPLES = Path(__file__).resolve().parent
CLI = EXAMPLES.parents[1] / "scripts" / "run.py"


def invoke(*arguments, code=0):
    result = subprocess.run([sys.executable, str(CLI), *map(str, arguments)], capture_output=True,
                            encoding="utf-8", timeout=180)
    if result.returncode != code:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stderr if code else result.stdout)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(work):
    results = []
    for theme in ("tide", "terrace"):
        bundle = work / f"{theme}-options"
        brief = EXAMPLES / f"{theme}-brief.json"
        handoff = work / theme
        invoke("generate", EXAMPLES / f"{theme}-project.json", "--layouts", EXAMPLES / "layouts.json",
               "--brief", "--out", bundle)
        result = invoke("deliver", bundle, "--brief", brief, "--azimuth", 45, "--out", handoff)
        assert result["rendering"] == "local_materials"
        data = load(handoff / "整区物件.json")
        assert data["local_materials"]["applied"] == ["D001", "D004"]
        assert not data["local_materials"]["out_of_view"]
        assert all(row["artwork_source"] == "local_drawing" and row["local_view"] for row in data["patterns"])
        assert result["layout_sha256"] == data["layout_sha256"]
        for name, expected in result["files"].items():
            assert hashlib.sha256((handoff / name).read_bytes()).hexdigest() == expected
        with (handoff / "逐件摆放.csv").open(encoding="utf-8-sig", newline="") as source:
            assert len(list(csv.reader(source))) == 1 + result["positioned_objects"]
        results.append((data, result))
    first, second = results
    assert first[0]["outline"] == second[0]["outline"]
    assert first[0]["positioning"]["rows"] == [
        {**b, "name": a["name"]} for a, b in zip(first[0]["positioning"]["rows"], second[0]["positioning"]["rows"],
                                                 strict=True)]
    assert first[1]["files"]["本地贴图三维.png"] != second[1]["files"]["本地贴图三维.png"]
    invalid = deepcopy(load(EXAMPLES / "tide-brief.json"))
    invalid["patterns"][0]["artwork"]["layers"][0]["points"][0][0] = -1
    bad = work / "bad.json"
    bad.write_text(json.dumps(invalid), encoding="utf-8")
    error = invoke("deliver", work / "tide-options", "--brief", bad, "--out", work / "rejected", code=2)
    assert "图层坐标" in error["message"] and not (work / "rejected").exists()
    print("Two local art designs, identical positioning, valid checksums and invalid artwork rejected.")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="opc-public-check-") as output:
        check(Path(output))
