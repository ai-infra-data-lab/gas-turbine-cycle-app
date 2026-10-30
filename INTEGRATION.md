# 独立計算モジュールの入出力契約

Module ID: `gt.cycle.simple_single_shaft` / Module version: `0.6.0` / schema_version: `6`。

## 呼出し

親フォルダがPythonの検索パスにある場合、次のようにパッケージとして呼び出せます。`model` 等の一般的な名前を持つ他アプリと混在しても、パッケージ内の定義を参照します。

```python
from gas_turbine_cycle_app import default_input, run_case

inputs = default_input()
inputs["parameters"]["compressor_pressure_ratio"] = 5.0
case = run_case(inputs)
```

`model.default_input()` で完全な入力を取得し、`model.validate_input(input)` で検証。`engine.analyze(input)` は結果dict、`engine.run_case(input)` は実行メタデータ付きの交換用ケースを返します。計算エンジンはUI・DB・ネットワークを呼びません。入力を変更しません。

すべてのparametersは有限の数値。パラメータ名に単位を含め、Pa・K・kg/s・W・J/kg・J/(kg K)を使用。効率・損失率は0〜1の小数。画面だけでkPa・kW・MJ/kg・%へ変換します。圧力比・燃空比は無次元。全圧は絶対圧、温度は全温（静止大気・低速排気境界を除く）。`shaft_speed_rpm` は冷却ポンピング仕事、および任意の段平均径による周速・負荷係数に使用します。ポンピング未使用では回転数だけでサイクル性能を変更しません。

入力は `schema_version, name, mode, parameters, context, thermodynamics, staging, elements, cooling`。

`thermodynamics = {model: "nasa9_frozen"|"constant_cp", fuel: "methane"|"gasturb_generic"|"gasturb_natural_gas"|"custom_ch", fuel_hydrogen_mass_fraction: 0..1}`。水素質量割合はcustom_ch時のみ使用し、残りは炭素です。燃料LHVはparametersの独立入力です。NASAモデルではlegacyのcp/R入力を計算に使用しませんが、交換用に維持・検証します。旧schema 1入力はconstant_cpへ正規化するため、従来の意味を維持します。旧module 0.1.0の交換ケースも入力を取り出して再計算できます。既定`default_input()`はNASA、`default_input("constant_cp")`は旧モデルです。

modeは `airflow` または `target_power`。入力の欠落・未知のパラメータ・文字列数値・bool・NaN/Inf・範囲外を拒否します。非アクティブ側の流量/目標出力も再利用用入力として保存・検証しますが計算には使用しません。

`context = {project_id: UUID|null, component_id: UUID|null, source_revision: int|null}`。案件IDがあるときだけ部品IDや元版を指定できます。部品IDは案件内で識別します。単独利用はすべてnull。現段階で管理DBのIDの存在照会はしません。

## 出力

交換用ケースは `format: gas-turbine-cycle-case, schema_version, module_id, module_version, run_id, started_at, completed_at, input_hash, input, result`。

- `input_hash` は正規化・ソートした完全な入力（context・ケース名・非アクティブ項目も含む）のSHA-256。浮動小数点の入力はfloatへ正規化する。
- `result.status`: `success` または `non_generating`。入力の不成立は `InputError` を送出（HTTPでは400）。
- `metrics`: SIベースで単位を含む項目名。`heat_rate_kj_kwh`, `specific_fuel_consumption_kg_kwh` の単位は名称のとおり。未定義はnull。
- `stations`: 0〜5の位置IDと名前、全温K、全圧Pa、質量流量kg/s、air/gas。さらにcp、cv、gamma、R、分子量、顕熱h、エントロピー温度関数。単位はキーの接尾辞で明示。位置番号は本モジュール固有。エントロピー温度関数は圧力項と混合定数を含まないため、異なる組成間の絶対エントロピー比較には使わない。
- `thermodynamics`: 実際のモデル・燃料、燃料C/H、空気/生成ガスの質量分率、当量比、NASAデータのコミット・元ファイルSHA-256・温度範囲等。constant_cpでは組成データを持たない。
- 再現性の照合にはinput_hashに加えてmodule_versionとresult.thermodynamics.dataset.source_file_sha256を保持する。入力ハッシュ単独では係数・計算コードの版を識別しない。
- `energy_balance`: 各損失・排気正味顕熱・電気補機と、質量/軸/電気エネルギー収支残差。
- `warnings`: 物性モデル等の限界と計算条件の注意点。

負の軸出力ではgenerator_loss/gross_electric/net_electricもnull（モーター領域のモデルなし）。正の軸出力でも補機のためnet_electric<=0なら効率・熱消費率・燃料消費率はnull。

## 将来の管理アプリ接続

1. 管理側で、明示的に単位・全温/静温・全圧/静圧・全周流量等を確認して数値入力へ変換する。文字列の自動推測変換はしない。
2. 管理案件・対象部品・revisionをcontextへ入れ、入力スナップショットを固定して実行する。
3. 管理側の `extensions["gt.cycle.simple_single_shaft"]` などへ、計算ケース全体か不変成果物への参照を保管する。現在の最大JSONサイズ等は管理側の検証を維持する。
4. revisionまたは入力ハッシュの変更後は、その計算を「更新必要」と扱う。
5. 設計値表への採用はユーザーが明示し、run_id・モデル版・単位・出典を残す。「計算成功」と「設計成立・実機検証済み」は別に管理する。

このアプリは管理アプリのimport形式を偽装して自動取込せず、管理DBを書き換えない。現時点では直接連携未実装。

## 圧力比スイープ

`engine.sweep(input, minimum, maximum, count)`。基準点の計算後空気流量を固定してairflowモードへ変換し、圧力比だけを変える。入力ハッシュと固定流量を返す。不成立点はnullと理由を返し、その他の点は継続する。機器の性能マップや回転数、形状による作動点の連続予測ではない。

## 段構成と段間出力（schema 3）

`staging`はcompressor/turbineを持つ。各機器は`{count: int(1..30), distribution: "pressure"|"work", weights: [positive_number]*count, mean_diameters_m: null|[positive_m]*count}`。重みは0.001〜1000、径は0.001〜20 m。APIでは配列を明示し、画面の空欄だけが均等重みへ変換される。全体性能は既存parametersで決まり、段構成はその後の熱力学的分解に使用する。詳細はSTAGING.md。

`result.stage_results.compressor/turbine`に、一定ポリトロープ効率、指定/再構成した全体効率、全体圧力比、比仕事、総仕事率、段仕事率合計、`stages`と`states`を記録。各段は`stage`（1始まり）、`inlet/outlet`（全温・全圧・流量・cp/cv/γ/R/h等）、圧縮比/膨張比、正の比仕事、仕事率、機械伝達後仕事率、段ηtt、等エントロピー出口温度、仕事割合、対数圧力割合、エントロピー生成、径、周速、ψを含む。SI単位・有限数値、未指定の径/U/ψはnull。タービンの質量流量は燃料を含む。`states`は入口と各段出口のN+1個。

入力ハッシュには段構成全体も含む。旧schema 1/2は各1段・対数圧力均等へ正規化し、元の物性モデルを維持する。モジュール0.1.0/0.2.0のケースJSONも入力を取り出して再計算する。旧版保存DBのレコードは変更せず、画面で開くと新しい版での再計算を要求する。

## 要素モデル（schema 4）

`elements.compressor/turbine = {enabled: bool, inlet_velocity_m_s: number, stages: [stage_config]}`。有効時はstagingの段数と配列長が一致し、distribution="pressure"が必要。無効時も再利用用設定を保持・型検証する。`model.element_stage(kind)`で1段の完全な設定を取得できる。

段設定は`rotor_efficiency, rotor_exit_velocity_m_s, diffuser`と、圧縮機の`return_channel`またはタービンの`nozzle`。静止要素の設定は`{enabled, model: "cp"|"loss", coefficient, exit_velocity_m_s}`。Vは1〜600 m/s、ロータηは0.5〜1、Cpは−5〜5、ζは0〜10。要素入口/出口でM<0.95、NASA適用範囲、正の圧力・温度、静止要素で全圧が増加しないことを検証する。

有効時はparametersのcompressor_efficiency/turbine_efficiencyを計算に使用しない。結果metricsの`compressor_effective_efficiency/turbine_effective_efficiency`が実際の全体ηtt。`stage_results`のmethod="coefficient_element_network"、specified_overall_isentropic_efficiency=null、recovered_overall_isentropic_efficiencyが実際の値。polytropic_efficiencyは全体端点からの等価値で、一定ηp経路は仮定しない。

`stage_results.*.elements[]`はstage、name、kind（rotor/stationary）、inlet/outlet、specific_work_j_kg、power_w、transmitted_power_w、係数指定/実際のCp/ζ、全圧損失、ロータ効率/圧力比、エントロピー生成を持つ。ロータではCp/ζ/全圧損失をnullとし、圧力上昇を損失と混同しない。静止要素では仕事を0とする。各状態には静温/静圧、絶対流速、密度、動圧、Mach、流量、換算面積を追加する。元素構成/質量は各機器内で固定。

旧schema 1/2/3は従来モデルとして正規化し、要素モードを無効にする。v0.1.0〜0.3.0の計算ケースも入力を取り出して再計算できる。ハッシュに要素設定全体を含み、データ/計算モデル版も保存する。式と制約はELEMENTS.md。

## 冷却（schema 5）

`cooling={enabled, temperature_basis: "combustor_exit"|"first_rotor_inlet", combustor_exit_limit_k, circuits:[]}`。流路テンプレートは`model.cooling_circuit()`。各流路はname、source_stage、fraction（入口空気基準）、target_stage、injection（before_nozzle/before_rotor/after_rotor/after_diffuser）、pressure_loss_fraction、heat_j_kg（外部熱）、mix_pressure_loss_fraction、required_pressure_margin、mixed_velocity_m_sを持つ。有効かつ正流量時はNASA物性・タービン要素モードを必須とする。詳細はCOOLING.md。

`result.cooling`に供給流路、外部熱、各質量流量、温度基準、主流/冷却の等エントロピー仕事ポテンシャル、効率定義を保存。metricsに燃焼器空気/冷却流量、燃焼器出口/1段目ロータ入口温度、燃焼器空気基準燃空比、熱力学効率、総入熱基準発電効率を追加。従来fuel_air_ratioは入口空気基準を維持。

冷却タービンのmethod="cooled_element_network"。単一入口の全体ηttと等価ηpはnull、thermodynamic_efficiencyを別記。段内混合のある段ηttもnull。各状態/段/要素の実mass_flow_kg_sとpower_wを使い、一定流量で仕事を再構成しない。specific_work_j_kgは段/要素の入口質量基準、work_per_inlet_air_j_kgは圧縮機入口空気基準。エントロピー流量生成はentropy_generation_w_k。

mixing要素はroute_indexでresult.cooling.routesへ対応。冷却流量、供給温度/圧力、必要供給圧、混合エネルギー残差、混合後組成を記録。圧縮機段のbleed_fractionとdownstream_mass_flow_factorは入口空気基準。energy_balance.cooling_external_heat_wは符号付きの外部熱。

旧schema 1〜4は冷却なしとして正規化。旧v0.4.0ケースも入力を取り出して再計算できる。冷却率がすべて0なら従来計算をそのまま使う。ハッシュに冷却設定全体を含む。


## v0.6: schema 6 の追加契約

旧schema 1〜5と旧0.1〜0.5のケースJSONを取込可能。schema 5の冷却条件は維持し、各流路の`thermal`/`pumping`を未使用として移行。現在の入出力はschema 6、module_version 0.6.0。旧結果は再計算を要求する。

- `cooling.circuits[].thermal`: enabled、mode（fixed_flow / metal_limit）、熱伝達率W/(m² K)、面積m²、厚さm、熱伝導率W/(m K)、film_effectiveness、metal_limit_k、min_fraction/max_fraction（入口空気基準の割合）。全フィールド名は`blade_cooling.thermal_defaults()`参照。
- `cooling.circuits[].pumping`: enabled、inlet_radius_m/outlet_radius_m、inlet_swirl_ratio/outlet_swirl_ratio、isentropic_efficiency。半径は軸中心基準、旋回比は絶対Cθ/U。
- 結果`cooling.routes[]`: circuit_index（元の流路配列の0始まり位置）、pre_pump_temperature_k/pressure_pa、pumping_specific_work_j_kg、pumping_power_w、thermal（伝熱量・金属/流体温度・余裕・判定・残差）。indexは正の抽気流だけを並べた結果配列の位置。
- 逆算は`cooling.flow_sizing`: circuit_index、fraction（計算された抽気率）、status、metal_limit_k、requested_fraction。入力JSONのfractionは変更しない。結果routes[].input.fractionは実際の計算率を記録し、熱モードは内部の固定流量試行値となる。再現にはケースのトップレベルinputを使う。
- metrics.cooling_pumping_power_w / blade_heat_transfer_w、energy_balance.cooling_pumping_power_wを追加。net_shaft_power_wは機械伝達後のガス仕事−圧縮機仕事−冷却ポンピング。
- 熱力学効率の分子は正味流体仕事（ガスロータ仕事−ポンピング）、分母はポンプ前の個別入口仕事ポテンシャル。GasTurb内部の補正効率との同一性は保証しない。
- `stage_results.turbine.elements[]`の混合点thermal、heat_entropy_generation_w_k、post_heat_mixing_entropy_generation_w_kで内部熱交換と混合を分離。既存entropy_generation_w_kは両者を含む節点全体値。

全設定の出典・適用範囲と材料温度限界は統合側で設計情報として保持できる。実形状・実験相関への直接接続は未実装。管理アプリのDBは変更しない。


## 独立モジュール: 軸締結の概略静強度

`shaft_joints.py`はcycleとは別の`gt.mechanical.shaft_joint` / module_version 0.1.0 / schema 1。`default_input(kind)`, `validate_input`, `analyze`, `run_case`, `read_input_file`を公開。kindはspline/curvic。入出力全量・単位・安全率・対象外事項は`SHAFT_JOINTS.md`。サイクルのmodule_version/schemaは変更しない。

入力はschema_version/kind/name/context/load/geometry/factors/material、curvicはclampを追加。fieldsとdefaultは`/api/joints/schema`から取得。結果チェックはdemand/allowable（SI）、required_safety、safety_factor、margin、utilization、passed。statusはwithin_preliminary_limits/exceeds_preliminary_limitsで、規格適合や実機安全を表さない。

ケース形式はgas-turbine-shaft-joint-case。案件・部品UUID・元リビジョンをcycleと同じcontext形式で保持。トルク・材料許容値の出典を入力文字列で記録し、SHA-256で正規化入力全量を識別する。保存は別DBファイル、cycle/管理アプリのDBには計算結果を書き込まない。サイクル荷重取込では保存ケースID・入力ハッシュ・軸位置をsource_noteへ保持する。
