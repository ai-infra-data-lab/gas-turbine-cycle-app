# 独立計算モジュールの入出力契約

Module ID: `gt.cycle.simple_single_shaft` / Module version: `0.1.0` / schema_version: `1`。

## 呼出し

親フォルダがPythonの検索パスにある場合、次のようにパッケージとして呼び出せます。`model` 等の一般的な名前を持つ他アプリと混在しても、パッケージ内の定義を参照します。

```python
from gas_turbine_cycle_app import default_input, run_case

inputs = default_input()
inputs["parameters"]["compressor_pressure_ratio"] = 5.0
case = run_case(inputs)
```

`model.default_input()` で完全な入力を取得し、`model.validate_input(input)` で検証。`engine.analyze(input)` は結果dict、`engine.run_case(input)` は実行メタデータ付きの交換用ケースを返します。計算エンジンはUI・DB・ネットワークを呼びません。入力を変更しません。

すべてのparametersは有限の数値。パラメータ名に単位を含め、Pa・K・kg/s・W・J/kg・J/(kg K)を使用。効率・損失率は0〜1の小数。画面だけでkPa・kW・MJ/kg・%へ変換します。圧力比・燃空比は無次元。全圧は絶対圧、温度は全温（静止大気・低速排気境界を除く）。`shaft_speed_rpm` は記録用、計算結果に影響しません。

入力は `schema_version, name, mode, parameters, context`。modeは `airflow` または `target_power`。入力の欠落・未知のパラメータ・文字列数値・bool・NaN/Inf・範囲外を拒否します。非アクティブ側の流量/目標出力も再利用用入力として保存・検証しますが計算には使用しません。

`context = {project_id: UUID|null, component_id: UUID|null, source_revision: int|null}`。案件IDがあるときだけ部品IDや元版を指定できます。部品IDは案件内で識別します。単独利用はすべてnull。現段階で管理DBのIDの存在照会はしません。

## 出力

交換用ケースは `format: gas-turbine-cycle-case, schema_version, module_id, module_version, run_id, started_at, completed_at, input_hash, input, result`。

- `input_hash` は正規化・ソートした完全な入力（context・ケース名・非アクティブ項目も含む）のSHA-256。浮動小数点の入力はfloatへ正規化する。
- `result.status`: `success` または `non_generating`。入力の不成立は `InputError` を送出（HTTPでは400）。
- `metrics`: SIベースで単位を含む項目名。`heat_rate_kj_kwh`, `specific_fuel_consumption_kg_kwh` の単位は名称のとおり。未定義はnull。
- `stations`: 0〜5の位置IDと名前、全温K、全圧Pa、質量流量kg/s、air/gas。位置番号は本モジュール固有。
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
