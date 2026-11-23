"""Generate reproducible reference checks, not a real-engine validation."""
import json
import math
from pathlib import Path
from engine import solve
from model import sample
from test_engine import pipe_model


def main():
    root = Path(__file__).resolve().parent
    target = root / "artifacts"
    target.mkdir(exist_ok=True)
    g, r, t0, p0, m1, m2, area, f, d = 1.4, 287.05, 550, 500000, .2, .6, 1e-4, .02, .02
    def fanno(m):
        return (1-m*m)/(g*m*m)+(g+1)/(2*g)*math.log((g+1)*m*m/(2+(g-1)*m*m))
    length = (fanno(m1)-fanno(m2))*d/f
    t1 = t0/(1+(g-1)/2*m1*m1)
    p1 = p0*(t1/t0)**(g/(g-1))
    pd = p1*m1/m2*math.sqrt((1+(g-1)/2*m1*m1)/(1+(g-1)/2*m2*m2))
    reference = p1/(r*t1)*m1*math.sqrt(g*r*t1)*area
    checks = []
    for n in (48, 96, 192):
        m = pipe_model(length_m=length, cells=n)
        m["nodes"][1]["pressure_pa"] = pd
        result = solve(m)["edges"][0]
        checks.append({"cells": n, "mass_flow_kg_s": result["mass_flow_abs"],
                       "relative_mass_error": result["mass_flow_abs"]/reference-1,
                       "exit_mach": result["max_mach"]})
    base = solve(sample())
    sample_grid = []
    for n in (48, 96, 192):
        m = sample()
        m["edges"][0]["cells"] = n
        result = solve(m)
        sample_grid.append({"cells": n, "supply_kg_s": result["summary"]["supply_kg_s"],
                            "heat_w": result["summary"]["heat_w"]})
    report = {"scope": "Analytic/reference and grid checks only; no real-engine or commercial-code validation.",
              "fanno": {"inlet_mach": m1, "exit_mach": m2, "length_m": length,
                        "backpressure_pa": pd, "reference_mass_flow_kg_s": reference, "checks": checks},
              "sample_summary": base["summary"], "sample_grid": sample_grid}
    (target / "reference_checks.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (target / "sample_result.json").write_text(json.dumps(base, indent=2, ensure_ascii=False), encoding="utf-8")
    (root / "examples/sample_network.json").write_text(json.dumps(sample(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
