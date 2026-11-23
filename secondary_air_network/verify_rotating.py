"""Write the rotating sample and reproducible numeric reference artifacts."""
import json
from pathlib import Path
from model import rotating_sample
from engine import solve,sweep


def main():
    root=Path(__file__).resolve().parent
    artifacts=root/"artifacts";artifacts.mkdir(exist_ok=True)
    m=rotating_sample()
    (root/"examples/rotating_network.json").write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding="utf-8")
    base=solve(m)
    grids=[]
    for cells in (48,96,192):
        m["edges"][0]["cells"]=cells
        out=solve(m)
        grids.append({"cells":cells,"supply_kg_s":out["summary"]["supply_kg_s"],"shaft_work_w":out["summary"]["shaft_work_w"],"outer_static_pressure_pa":out["nodes"][1]["pressure_pa"]})
    study=sweep(rotating_sample(),{"group":"rotation","id":"rot_in","field":"rpm"},[0,15000,30000])
    report={"scope":"Co-rotating adiabatic model; analytic/conservation/grid checks only. No real-engine validation.","sample_summary":base["summary"],"grid":grids,"rpm_sweep":study}
    (artifacts/"rotating_result.json").write_text(json.dumps(base,ensure_ascii=False,indent=2),encoding="utf-8")
    (artifacts/"rotating_checks.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"sample_summary":base["summary"],"grid":grids},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
