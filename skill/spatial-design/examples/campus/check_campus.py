import json, subprocess, sys, tempfile
from copy import deepcopy
from pathlib import Path
import ezdxf
from PIL import Image
root=Path(__file__).resolve().parents[2]
skill=root
cli=skill/"scripts"/"run.py"
sample=json.loads((skill/"examples/campus/campus.json").read_text(encoding="utf-8"))
def run(work, name, data, code):
    source=work/(name+".json")
    output=work/(name+"-out")
    source.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    result=subprocess.run([sys.executable,str(cli),"campus",str(source),"--out",str(output)],
                          capture_output=True,text=True,encoding="utf-8",timeout=180)
    assert result.returncode==code,(result.stdout,result.stderr)
    return json.loads(result.stderr if code else result.stdout), output
with tempfile.TemporaryDirectory(prefix="spatial-campus-") as temporary:
    work=Path(temporary)
    result,out=run(work,"positive",sample,0)
    assert result["status"]=="ready" and result["area_m2"]==8000
    assert all(x["reachable"] for x in result["buildings"])
    doc=ezdxf.readfile(out/"园区总平面.dxf")
    assert doc.units==ezdxf.units.MM
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="BUILDINGS"]'))==3
    for file in ("园区体量三维.png","园区总平面.png"):
        with Image.open(out/file) as image:
            assert image.width>=1500
            image.verify()
    broken=deepcopy(sample)
    broken["paths"][0]["points"]=[[0,40000],[100000,40000]]
    broken["paths"][1]["points"]=[[50000,40000],[50000,8000]]
    broken["entries"][0]["point"]=[0,40000]
    result,_=run(work,"disconnected",broken,0)
    assert result["status"]=="needs_revision" and not any(x["reachable"] for x in result["buildings"])
    bad=deepcopy(sample)
    bad["buildings"][0]["polygon"] = bad["buildings"][1]["polygon"]
    bad["buildings"][0]["entrance"] = bad["buildings"][1]["entrance"]
    assert "重叠" in run(work,"overlap",bad,2)[0]["message"]
    bad=deepcopy(sample)
    bad["paths"][0]["points"]=[[0,500],[100000,500]]
    assert "超出园区边界" in run(work,"wide",bad,2)[0]["message"]
    bad=deepcopy(sample)
    bad["confirmed"]=False
    assert "核对园区" in run(work,"unconfirmed",bad,2)[0]["message"]
    bad=deepcopy(sample)
    bad["buildings"][0]["height_mm"]=None
    assert "建筑高度" in run(work,"height",bad,2)[0]["message"]
    bad=deepcopy(sample)
    bad["buildings"][0]["entrance"]=[20000,47000]
    assert "建筑轮廓上" in run(work,"entrance",bad,2)[0]["message"]
    print("8000m²: plan, 3D, DXF and connectivity passed; 6 negative/control cases passed.")
