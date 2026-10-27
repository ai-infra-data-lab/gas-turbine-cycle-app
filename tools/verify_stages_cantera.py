"""Optional independent NASA stage-path inversion and finite-stage-efficiency check."""
import argparse
import json
import math
from pathlib import Path

from verify_cantera import phase_for, ct, air, products, DATA
from engine import analyze
from model import default_input


def verify():
    cases = []
    max_t = max_p = max_eta = 0.0
    points = 0
    for mode in ("pressure", "work"):
        for weights in ([1, 1, 1, 1], [1, 2, 3, 1]):
            inp = default_input()
            for x in inp["staging"].values():
                x.update(count=4, distribution=mode, weights=weights)
            result = analyze(inp)
            a, g = air(), products(result["metrics"]["fuel_air_ratio"], inp["thermodynamics"])
            for key, mix in (("compressor", a), ("turbine", g)):
                x = result["stage_results"][key]
                first, last = x["states"][0], x["states"][-1]
                phase, _, _ = phase_for(mix)
                phase.TP = first["total_temperature_k"], 100000
                s0, h0 = phase.entropy_mass, phase.enthalpy_mass
                phase.TP = last["total_temperature_k"], 100000
                s1, h1 = phase.entropy_mass, phase.enthalpy_mass
                cumulative = 0.0
                for i, state in enumerate(x["states"]):
                    if i:
                        cumulative += weights[i-1]/sum(weights)
                    if mode == "pressure":
                        phase.SP = s0+(s1-s0)*cumulative, 100000
                        p = first["total_pressure_pa"] * (last["total_pressure_pa"]/first["total_pressure_pa"])**cumulative
                    else:
                        phase.HP = h0+(h1-h0)*cumulative, 100000
                        p = first["total_pressure_pa"] * math.exp(math.log(last["total_pressure_pa"]/first["total_pressure_pa"])*(phase.entropy_mass-s0)/(s1-s0))
                    max_t = max(max_t, abs(phase.T-state["total_temperature_k"]))
                    max_p = max(max_p, abs(p-state["total_pressure_pa"])/p)
                    points += 1
                for row in x["stages"]:
                    phase.TP = row["inlet"]["total_temperature_k"], row["inlet"]["total_pressure_pa"]
                    hi, si = phase.enthalpy_mass, phase.entropy_mass
                    phase.TP = row["outlet"]["total_temperature_k"], row["outlet"]["total_pressure_pa"]
                    ho = phase.enthalpy_mass
                    phase.SP = si, row["outlet"]["total_pressure_pa"]
                    eta = (phase.enthalpy_mass-hi)/(ho-hi) if key == "compressor" else (hi-ho)/(hi-phase.enthalpy_mass)
                    max_eta = max(max_eta, abs(eta-row["isentropic_efficiency"]))
                cases.append({"component": key, "distribution": mode, "weights": weights,
                              "overall_efficiency": x["recovered_overall_isentropic_efficiency"],
                              "polytropic_efficiency": x["polytropic_efficiency"]})
    assert max_t < 2e-5 and max_p < 2e-8 and max_eta < 2e-7
    return {"reference": "Cantera SP/HP and entropy state functions", "cantera_version": ct.__version__,
            "dataset_id": DATA["dataset_id"], "states_checked": points, "cases": cases,
            "maximum_temperature_error_k": max_t, "maximum_pressure_relative_error": max_p,
            "maximum_stage_efficiency_absolute_error": max_eta,
            "limitations": "Same NASA coefficients and overall endpoints/composition; independently verifies allocation-path inversion and finite stage efficiencies, not aerodynamic validity or an actual engine stage distribution."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k.startswith("maximum") or k == "states_checked"}))
