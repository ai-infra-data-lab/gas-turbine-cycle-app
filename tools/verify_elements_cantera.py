"""Optional independent Cantera check of rotor/passive element states and closure."""
import argparse
import json
import math
from pathlib import Path
from verify_cantera import phase_for, ct, air, products, DATA
from engine import analyze
from model import default_input, element_stage


def reference_state(gas, tt, pt, velocity):
    phase, scale, _ = phase_for(gas)
    phase.TP = tt, pt
    h0, s0 = phase.enthalpy_mass, phase.entropy_mass
    phase.HP = h0-velocity**2/(2*scale), pt
    ts = phase.T
    ps = pt*math.exp((phase.entropy_mass-s0)/(ct.gas_constant/phase.mean_molecular_weight))
    rho = ps/(gas.r*ts)
    return {"total_temperature_k": tt, "total_pressure_pa": pt, "static_temperature_k": ts,
            "static_pressure_pa": ps, "dynamic_pressure_pa": .5*rho*velocity**2, "velocity_m_s": velocity}


def verify():
    temperature_error = pressure_error = max_closure = 0.
    records = []
    checked = 0
    for fuel in ("methane", "gasturb_generic"):
        for count in (1, 2):
            inp = default_input()
            inp["thermodynamics"]["fuel"] = fuel
            for key in ("compressor", "turbine"):
                inp["staging"][key].update(count=count, weights=[1]*count)
                inp["elements"][key].update(enabled=True, stages=[element_stage(key) for _ in range(count)])
            r = analyze(inp)
            for key, gas in (("compressor", air()), ("turbine", products(r["metrics"]["fuel_air_ratio"], inp["thermodynamics"]))):
                train = r["stage_results"][key]
                first = train["states"][0]
                current = reference_state(gas, first["total_temperature_k"], first["total_pressure_pa"], first["velocity_m_s"])
                for row in train["elements"]:
                    tt, pt = current["total_temperature_k"], current["total_pressure_pa"]
                    velocity = row["outlet"]["velocity_m_s"]
                    if row["kind"] == "rotor":
                        ratio, eta = row["rotor_pressure_ratio"], row["rotor_isentropic_efficiency"]
                        pnew = pt*ratio if key == "compressor" else pt/ratio
                        phase, _, _ = phase_for(gas)
                        phase.TP = tt, pt
                        hb, sb = phase.enthalpy_mass, phase.entropy_mass
                        phase.SP = sb, pnew
                        hs = phase.enthalpy_mass
                        phase.HP = hb+(hs-hb)/eta if key == "compressor" else hb-eta*(hb-hs), pnew
                        current = reference_state(gas, phase.T, pnew, velocity)
                    else:
                        coef = row["specified_coefficient"]
                        if row["coefficient_model"] == "loss":
                            pnew = pt-coef*current["dynamic_pressure_pa"]
                        else:
                            target_static = current["static_pressure_pa"]+coef*current["dynamic_pressure_pa"]
                            unit_state = reference_state(gas, tt, 100000, velocity)
                            pnew = target_static/(unit_state["static_pressure_pa"]/100000)
                        current = reference_state(gas, tt, pnew, velocity)
                    for field in ("total_temperature_k", "static_temperature_k"):
                        temperature_error = max(temperature_error, abs(current[field]-row["outlet"][field]))
                    for field in ("total_pressure_pa", "static_pressure_pa", "dynamic_pressure_pa"):
                        pressure_error = max(pressure_error, abs(current[field]-row["outlet"][field])/max(abs(current[field]),1))
                    checked += 1
                target = r["stations"][2 if key == "compressor" else 4]["total_pressure_pa"]
                closure = abs(current["total_pressure_pa"]-target)/target
                max_closure = max(max_closure, closure)
                records.append({"fuel": fuel, "stage_count": count, "machine": key, "reference_exit_Tt_k": current["total_temperature_k"],
                                "reference_exit_Pt_pa": current["total_pressure_pa"], "endpoint_pressure_relative_error": closure})
    assert temperature_error < 2e-5 and pressure_error < 2e-7 and max_closure < 2e-7
    return {"reference": "Cantera 3.2.0 HP/SP and entropy static-total conversion", "dataset_id": DATA["dataset_id"],
            "elements_checked": checked, "maximum_temperature_error_k": temperature_error,
            "maximum_pressure_relative_error": pressure_error, "maximum_endpoint_pressure_relative_error": max_closure,
            "cases": records, "limitations": "Same NASA coefficients, gas composition, fuel-air ratio and solved rotor pressure ratios. Independently propagates rotor and stationary states and checks final pressure closure; not a separate aerodynamic model, geometry validation or experiment."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in data.items() if k.startswith("maximum") or k == "elements_checked"}))
