"""NASA CEA NASA9 caloric properties and lean, frozen C/H combustion products.

No chemical-equilibrium solver is claimed. Data provenance is shipped with the coefficients.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

if __package__:
    from .model import InputError
else:
    from model import InputError

T_REF_K = 298.15
DATA_PATH = Path(__file__).resolve().parent / "thermo_data/nasa9_species.json"
DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"))
RU = DATA["universal_gas_constant_j_kmol_k"]
T_MIN, T_MAX = DATA["temperature_range_k"]
AIR_MOLE_FRACTIONS = {"N2": .78084, "O2": .209476, "Ar": .009365, "CO2": .000319}
ATOMIC_C = 12.0107
ATOMIC_H = 1.00794
FUEL_H = {"methane": 4 * ATOMIC_H / (ATOMIC_C + 4 * ATOMIC_H), "gasturb_generic": .1392,
          "gasturb_natural_gas": .9 * 4 * ATOMIC_H / (ATOMIC_C + 4 * ATOMIC_H)
          + .1 * 6 * ATOMIC_H / (2 * ATOMIC_C + 6 * ATOMIC_H)}
FUEL_NAMES = {"methane": "メタン CH₄", "gasturb_generic": "GasTurb Generic：C 86.08 / H 13.92 質量%",
              "gasturb_natural_gas": "GasTurb天然ガス組成：CH₄ 90 / C₂H₆ 10 質量%", "custom_ch": "C/H燃料（組成を指定）"}


@lru_cache(maxsize=16384)
def species_properties(name, temperature):
    if name not in DATA["species"]:
        raise InputError("対応していないNASA化学種です。")
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or not T_MIN <= temperature <= T_MAX:
        raise InputError(f"NASA物性の対応温度は{T_MIN}〜{T_MAX} Kです。範囲外へ外挿しません。")
    item = DATA["species"][name]
    region = next((r for r in item["ranges"] if r["t_min_k"] <= temperature <= r["t_max_k"]), None)
    if region is None:
        raise InputError(f"{name}の物性係数の適用温度外です。")
    a, t = region["coefficients"], float(temperature)
    r = RU / item["molecular_weight_kg_kmol"]
    cp_r = a[0] / t**2 + a[1] / t + a[2] + a[3]*t + a[4]*t**2 + a[5]*t**3 + a[6]*t**4
    h_rt = -a[0] / t**2 + a[1]*math.log(t)/t + a[2] + a[3]*t/2 + a[4]*t**2/3 + a[5]*t**3/4 + a[6]*t**4/5 + a[7]/t
    s_r = -a[0]/(2*t**2) - a[1]/t + a[2]*math.log(t) + a[3]*t + a[4]*t**2/2 + a[5]*t**3/3 + a[6]*t**4/4 + a[8]
    return cp_r*r, h_rt*r*t, s_r*r


class ConstantGas:
    def __init__(self, cp, r):
        self.constant_cp, self.r = cp, r
        self.molecular_weight = RU / r

    def cp(self, t):
        return self.constant_cp

    def h(self, t):
        return self.constant_cp * (t - T_REF_K)

    def entropy_thermal(self, t):
        return self.constant_cp * math.log(t / T_REF_K)

    def gamma(self, t):
        cp = self.cp(t)
        return cp / (cp - self.r)


class Mixture:
    def __init__(self, masses):
        total = math.fsum(masses.values())
        if total <= 0 or any(not math.isfinite(v) or v < 0 for v in masses.values()):
            raise InputError("混合気の質量が不正です。")
        self.mass_fractions = {k: v / total for k, v in masses.items() if v > 0}
        self.r = math.fsum(y * RU / DATA["species"][k]["molecular_weight_kg_kmol"] for k, y in self.mass_fractions.items())
        self.molecular_weight = RU / self.r

    def cp(self, t):
        return math.fsum(y * species_properties(k, t)[0] for k, y in self.mass_fractions.items())

    def h(self, t):
        # Sensible enthalpy, not formation enthalpy. Never add formation energy to LHV a second time.
        return math.fsum(y * (species_properties(k, t)[1] - species_properties(k, T_REF_K)[1]) for k, y in self.mass_fractions.items())

    def entropy_thermal(self, t):
        # Difference at fixed composition; constant ideal-mixing terms cancel.
        return math.fsum(y * (species_properties(k, t)[2] - species_properties(k, T_REF_K)[2]) for k, y in self.mass_fractions.items())

    def gamma(self, t):
        cp = self.cp(t)
        return cp / (cp - self.r)


AIR_MW = math.fsum(x * DATA["species"][name]["molecular_weight_kg_kmol"] for name, x in AIR_MOLE_FRACTIONS.items())
AIR_MOLES_PER_KG = {name: x / AIR_MW for name, x in AIR_MOLE_FRACTIONS.items()}


def air():
    return Mixture({k: n * DATA["species"][k]["molecular_weight_kg_kmol"] for k, n in AIR_MOLES_PER_KG.items()})


def hydrogen_fraction(settings):
    return FUEL_H[settings["fuel"]] if settings["fuel"] != "custom_ch" else settings["fuel_hydrogen_mass_fraction"]


def stoichiometric_fuel_air_ratio(settings):
    h = hydrogen_fraction(settings)
    oxygen_per_fuel = (1 - h) / ATOMIC_C + h / (4 * ATOMIC_H)
    return AIR_MOLES_PER_KG["O2"] / oxygen_per_fuel


def products(f, settings):
    if not math.isfinite(f) or f < 0 or f > stoichiometric_fuel_air_ratio(settings) * (1 + 1e-12):
        raise InputError("燃空比が量論比を超えています。燃料過濃・不完全燃焼は未対応です。")
    h = hydrogen_fraction(settings)
    c_moles, water_moles = f * (1-h)/ATOMIC_C, f * h/(2*ATOMIC_H)
    moles = dict(AIR_MOLES_PER_KG)
    moles["CO2"] += c_moles
    moles["H2O"] = water_moles
    moles["O2"] = max(0, moles["O2"] - c_moles - .5 * water_moles)
    return Mixture({k: n * DATA["species"][k]["molecular_weight_kg_kmol"] for k, n in moles.items()})


def solve_temperature(function, target, lower=T_MIN, upper=T_MAX):
    flo, fhi = function(lower) - target, function(upper) - target
    if flo > 1e-7 or fhi < -1e-7:
        raise InputError("等エントロピー状態またはエンタルピーから求まる温度がNASA物性の適用範囲外です。")
    if abs(flo) < 1e-9:
        return lower
    if abs(fhi) < 1e-9:
        return upper
    for _ in range(65):
        middle = (lower + upper) / 2
        if function(middle) < target:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2


def properties_at(gas, temperature):
    cp = gas.cp(temperature)
    return {"cp_j_kg_k": cp, "cv_j_kg_k": cp - gas.r, "gamma": gas.gamma(temperature),
            "gas_constant_j_kg_k": gas.r, "molecular_weight_kg_kmol": gas.molecular_weight,
            "sensible_enthalpy_j_kg": gas.h(temperature), "entropy_thermal_function_j_kg_k": gas.entropy_thermal(temperature)}


def provenance():
    return {k: DATA[k] for k in ("dataset_id", "source_repository", "source_commit", "source_url", "source_file_sha256", "retrieved_date", "temperature_range_k", "universal_gas_constant_j_kmol_k")}
