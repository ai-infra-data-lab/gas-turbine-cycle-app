# NASA CEA thermodynamic coefficient data

`thermo_data/nasa9_species.json` is an extracted and reformatted subset of NASA CEA's `data/thermo.inp`:

- Repository: https://github.com/nasa/cea
- Pinned commit: `985fd33368b2334c4162a586c499e0ab41d12bd3`
- Original source: https://raw.githubusercontent.com/nasa/cea/985fd33368b2334c4162a586c499e0ab41d12bd3/data/thermo.inp
- Original file SHA-256: `fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363`
- Retrieved: 2026-10-27
- Reference: McBride, Zehe, Gordon, NASA/TP-2002-211556, https://ntrs.nasa.gov/api/citations/20020085330/downloads/20020085330.pdf
- Universal gas constant: `8314.51 J/(kmol K)`, matching the pinned CEA `source/bind/python/cea/constants.py`.

Modifications: five gas species (N2, O2, H2O, CO2, Ar) and their 200–6000 K intervals were selected; intervals above 6000 K were omitted; numeric coefficients were not changed; the data was reformatted as JSON. `tools/extract_nasa_data.py` reproduces this extraction from the original input file. The upstream file includes updates; this is a pinned NASA dataset, not a claim that it is byte-identical to the property tables distributed with GasTurb.

The upstream Apache-2.0 license and NOTICE are preserved in `third_party/nasa_cea/LICENSE.txt` and `third_party/nasa_cea/NOTICE.txt`. The attribution applies to the NASA-derived data; this file does not assign a new license to the rest of this application.

No GasTurb software, proprietary `.prp` tables, or text from its manual is bundled. Public documentation was consulted to identify its property-model approach and the two stated fuel compositions.

The optional verification tool uses Cantera 3.2.0 in a separate development environment. Cantera is not a runtime dependency or bundled distribution. https://cantera.org/
