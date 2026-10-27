"""Optional independent NASA9 evaluation/isentropic inversion check. Needs Cantera 3.2.

The application has no Cantera dependency. Run this in an isolated verification environment.
"""
import argparse
import json
import math
import sys
from itertools import product
from pathlib import Path

import cantera as ct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import analyze
from model import default_input
from thermo import DATA, RU, T_REF_K, air, products, species_properties

ATOMS = {"N2": {"N": 2}, "O2": {"O": 2}, "Ar": {"Ar": 1}, "CO2": {"C": 1, "O": 2}, "H2O": {"H": 2, "O": 1}}


def nasa_species(name):
    entry = DATA["species"][name]
    coefficients = [len(entry["ranges"])]
    for region in entry["ranges"]:
        coefficients.extend([region["t_min_k"], region["t_max_k"], *region["coefficients"]])
    species = ct.Species(name, ATOMS[name])
    species.thermo = ct.Nasa9PolyMultiTempRegion(200, 6000, 100000, coefficients)
    return species


def phase_for(mix):
    phase = ct.Solution(thermo="ideal-gas", species=[nasa_species(k) for k in ATOMS])
    # Convert using NASA molecular weights, not Cantera's slightly different atomic-weight table.
    phase.X = {k: y/DATA["species"][k]["molecular_weight_kg_kmol"] for k, y in mix.mass_fractions.items()}
    scale = RU / ct.gas_constant * phase.mean_molecular_weight / mix.molecular_weight
    phase.TP = T_REF_K, 100000
    href = phase.enthalpy_mass
    return phase, scale, href


def verify():
    errors = {"cp_relative": 0., "h_relative": 0., "s_relative": 0., "temperature_absolute_k": 0., "power_relative": 0.}
    count = 0
    for name, entry in DATA["species"].items():
        thermo = nasa_species(name).thermo
        factor = RU / ct.gas_constant / entry["molecular_weight_kg_kmol"]
        for t in (200, 298.15, 500, 999, 1001, 1500, 2000, 5000):
            ours = species_properties(name, t)
            reference = (thermo.cp(t)*factor, thermo.h(t)*factor, thermo.s(t)*factor)
            for key, a, b in zip(("cp_relative", "h_relative", "s_relative"), ours, reference):
                error = abs(a-b)/max(abs(b), 1)
                errors[key] = max(errors[key], error)
                # Element-reference enthalpies near zero need an absolute tolerance too.
                assert error < 2e-11 or abs(a-b) < 1e-6, (name, t, key, error)
            count += 1
    cases = []
    for fuel in ("methane", "gasturb_generic", "gasturb_natural_gas"):
        for pr, tit in product((3.8, 8), (1123, 1400)):
            inp = default_input()
            inp["thermodynamics"]["fuel"] = fuel
            inp["parameters"].update(compressor_pressure_ratio=pr, turbine_inlet_temperature_k=tit)
            result = analyze(inp)
            v, m, s = inp["parameters"], result["metrics"], result["stations"]
            a = air()
            ag, scale_a, href_a = phase_for(a)
            ag.TP = v["ambient_temperature_k"], s[1]["total_pressure_pa"]
            h1 = ag.enthalpy_mass
            ag.SP = ag.entropy_mass, s[2]["total_pressure_pa"]
            t2s, h2s = ag.T, ag.enthalpy_mass
            ag.HP = h1 + (h2s-h1)/v["compressor_efficiency"], s[2]["total_pressure_pa"]
            t2, h2 = ag.T, (ag.enthalpy_mass-href_a)*scale_a
            # Independently root the combustor balance using Cantera enthalpy calls.
            lo, hi = 0., result["thermodynamics"]["stoichiometric_fuel_air_ratio"]
            for _ in range(55):
                f = (lo+hi)/2
                gg, scale_g, href_g = phase_for(products(f, inp["thermodynamics"]))
                gg.TP = s[3]["total_temperature_k"], s[3]["total_pressure_pa"]
                residual = (1+f)*(gg.enthalpy_mass-href_g)*scale_g-h2-f*v["combustion_efficiency"]*v["fuel_lhv_j_kg"]
                if residual > 0:
                    lo = f
                else:
                    hi = f
            f = (lo+hi)/2
            gg, scale_g, href_g = phase_for(products(f, inp["thermodynamics"]))
            gg.TP = s[3]["total_temperature_k"], s[3]["total_pressure_pa"]
            h3ct = gg.enthalpy_mass
            gg.SP = gg.entropy_mass, s[4]["total_pressure_pa"]
            t4s, h4sct = gg.T, gg.enthalpy_mass
            gg.HP = h3ct-v["turbine_efficiency"]*(h3ct-h4sct), s[4]["total_pressure_pa"]
            t4 = gg.T
            ma = v["air_mass_flow_kg_s"]
            wc = ma*((h2s-h1)/v["compressor_efficiency"])*scale_a
            wt = ma*(1+f)*(h3ct-gg.enthalpy_mass)*scale_g
            net = (v["mechanical_efficiency"]*wt-wc)*v["generator_efficiency"]-v["auxiliary_power_w"]
            temp_error = max(abs(t2-s[2]["total_temperature_k"]), abs(t4-s[4]["total_temperature_k"]),
                             abs(t2s-m["compressor_isentropic_exit_temperature_k"]), abs(t4s-m["turbine_isentropic_exit_temperature_k"]))
            power_error = abs(net-m["net_electric_power_w"])/max(abs(net), 1)
            errors["temperature_absolute_k"] = max(errors["temperature_absolute_k"], temp_error)
            errors["power_relative"] = max(errors["power_relative"], power_error)
            assert temp_error < 2e-5 and power_error < 2e-7, (fuel, pr, temp_error, power_error)
            cases.append({"fuel": fuel, "pressure_ratio": pr, "turbine_inlet_temperature_k": tit, "cantera_net_power_w": net,
                          "temperature_max_error_k": temp_error, "power_relative_error": power_error})
    return {"reference": "Cantera 3.2.0 NASA9 evaluator and SP/HP state solvers", "cantera_version": ct.__version__,
            "dataset_id": DATA["dataset_id"], "dataset_source_sha256": DATA["source_file_sha256"],
            "species_temperature_pairs": count, "cycle_cases": cases, "maximum_errors": errors,
            "note": "Same pinned coefficients; NASA Ru/molecular-weight conventions explicitly reconciled. This is not a CEA execution or a GasTurb comparison."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["maximum_errors"]))
