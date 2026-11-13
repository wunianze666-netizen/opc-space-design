"""Cross-check independent campus designs and constrained counterexamples."""
from copy import deepcopy
import csv
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import ezdxf
from PIL import Image, ImageChops
from shapely.geometry import Point, Polygon

HERE = Path(__file__).resolve().parent
CLI = HERE.parents[1] / "scripts" / "run.py"

def invoke(folder, name, raw, code=0):
    input_path = folder / f"{name}.json"
    target = folder / name
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    process = subprocess.run([sys.executable, str(CLI), "campus", str(input_path), "--out", str(target)],
                             capture_output=True, encoding="utf-8", timeout=120)
    assert process.returncode == code, (process.stdout, process.stderr)
    return json.loads(process.stderr if code else process.stdout), target

def shifted(polygon, x, y):
    return [[px + x, py + y] for px, py in polygon]

def main(folder):
    summaries = {}
    images = []
    cases = (("tech", 8000), ("nature", 9600), ("community", 6400))
    for name, area in cases:
        raw = json.loads((HERE / "styles" / f"{name}.json").read_text(encoding="utf-8"))
        result, target = invoke(folder, name, raw)
        assert result["status"] == "ready" and result["area_m2"] == area
        assert len(result["features"]) == 20 and all(f["access"] == "平面直线可达" for f in result["features"])
        assert len(result["surfaces"]) == 2 and all(b["reachable"] for b in result["buildings"])
        assert (target / "户外设施局部-04.png").is_file()
        scene = json.loads((target / "园区三维几何.json").read_text(encoding="utf-8"))
        assert scene["units"] == "mm"
        for i, source in enumerate(raw["features"], 1):
            shape = Polygon(source["polygon"]).buffer(.001)
            surfaces = [face for face in scene["faces"] if face["owner"] == ["feature", f"E{i:03d}"]]
            assert surfaces
            for surface in surfaces:
                for x, y, z in surface["vertices"]:
                    assert shape.covers(Point(x, y)) and 0 <= z <= source["height_mm"]
        doc = ezdxf.readfile(target / "园区总平面.dxf")
        assert doc.units == ezdxf.units.MM
        geometry = list(doc.modelspace().query('LWPOLYLINE[layer=="FEATURES"]'))
        assert len(geometry) == len(raw["features"])
        for element, source in zip(geometry, raw["features"], strict=True):
            actual = Polygon([(p[0], p[1]) for p in element.get_points("xy")])
            assert actual.symmetric_difference(Polygon(source["polygon"])).area < 0.01
        assert len(doc.modelspace().query('POINT[layer=="FEATURE_ACCESS"]')) == 20
        with (target / "户外设施定位.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 20
        assert [row["编号"] for row in rows] == [f"E{i:03d}" for i in range(1, 21)]
        assert rows[2]["说明"] and all(row["操作点（图纸 X Y mm）"] for row in rows)
        with Image.open(target / "园区体量三维.png") as image:
            images.append(image.convert("RGB").copy())
        if name == "tech":
            assert len(result["features"][2]["components"]) == 3
            assert len(doc.modelspace().query('LWPOLYLINE[layer=="FEATURE_PARTS"]')) == 3
        summaries[name] = {"area_m2": area, "buildings": len(result["buildings"]), "features": len(rows),
                           "pages": 4}
    assert all(ImageChops.difference(images[i], images[j]).getbbox() for i in range(3) for j in range(i + 1, 3))
    raw = json.loads((HERE / "styles/tech.json").read_text(encoding="utf-8"))
    tests = []
    bad = deepcopy(raw)
    bad["features"][0]["polygon"] = shifted(bad["features"][0]["polygon"], 0, 2000)
    tests.append(("road_conflict", bad, "侵占道路"))
    bad = deepcopy(raw)
    bad["features"][0]["polygon"] = deepcopy(bad["features"][1]["polygon"])
    bad["features"][0].pop("access_point")
    tests.append(("object_overlap", bad, "其他设施重叠"))
    bad = deepcopy(raw)
    bad["features"][2]["components"][0]["polygon"] = shifted(bad["features"][2]["components"][0]["polygon"], 3000, 0)
    tests.append(("component_overflow", bad, "超出外廓"))
    bad = deepcopy(raw)
    bad["buildings"][0]["facade"]["head_mm"] = 99999
    tests.append(("bad_facade", bad, "窗顶"))
    bad = deepcopy(raw)
    bad["features"][0]["facing"] = "unknown"
    tests.append(("bad_direction", bad, "facing"))
    bad = deepcopy(raw)
    bad["features"][0]["access_point"] = [150000, 150000]
    tests.append(("bad_access", bad, "操作点"))
    for name, bad, expected in tests:
        error, target = invoke(folder, name, bad, 2)
        assert expected in error["message"] and not target.exists(), error
    optional = deepcopy(raw)
    optional["features"][0].pop("access_point")
    result, _ = invoke(folder, "unknown_access", optional)
    assert result["features"][0]["access"] == "待确认"
    low_maintenance = deepcopy(raw)
    low_maintenance["features"] = [row for row in low_maintenance["features"] if row["kind"] not in ("tree", "planter")]
    result, _ = invoke(folder, "low_maintenance", low_maintenance)
    assert all(row["kind"] not in ("tree", "planter") for row in result["features"])
    print(json.dumps({"scenarios": summaries, "rejected": [case[0] for case in tests],
                      "unknown_access": "待确认", "low_maintenance_features": len(result["features"])},
                     ensure_ascii=False))

if __name__ == "__main__":
    with TemporaryDirectory(prefix="spatial-campus-details-") as directory:
        main(Path(directory))
