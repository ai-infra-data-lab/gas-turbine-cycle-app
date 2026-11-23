# ガスタービン設計アプリ 共通マニュアル

## Windowsで起動する

GitHubから取得する場合は、アプリを置く共通フォルダーで次のコマンドを実行します。Cycle Studioには単管燃焼器と二次空気ネットワークが付属します。定格通しケースには軸流タービン、油配管、RotorLabも必要です。

```powershell
git clone https://github.com/ai-infra-data-lab/gas-turbine-cycle-app.git gas_turbine_cycle_app
git clone https://github.com/ai-infra-data-lab/gas-turbine-design-manager.git gas_turbine_manager
git clone https://github.com/ai-infra-data-lab/axial-turbine-designer.git axial_turbine_designer
git clone https://github.com/ai-infra-data-lab/pipe-pressure-loss-app.git pipe_pressure_loss_app
git clone https://github.com/ai-infra-data-lab/rotorlab.git rotordynamics_app
git clone https://github.com/ai-infra-data-lab/centrifugal-blade-designer.git centrifugal_blade_designer
```

各アプリのマニュアルに従ってPython環境を用意し、共通フォルダーからランチャーを実行します。Webアプリの接続先は起動時に表示される `127.0.0.1` です。

```powershell
python -m gas_turbine_cycle_app.tools.suite_launcher list
python -m gas_turbine_cycle_app.tools.suite_launcher start desk
python -m gas_turbine_cycle_app.tools.suite_launcher start cycle
python -m gas_turbine_cycle_app.tools.suite_launcher start combustor
```

| 起動名 | 用途 | 個別マニュアル |
| --- | --- | --- |
| `desk` | 案件、設計版、評価断面、成果物の管理と比較 | [Design Desk](https://github.com/ai-infra-data-lab/gas-turbine-design-manager) |
| `cycle` | エンジン性能と各設計アプリへの受け渡し | [Cycle Studio](README.md) |
| `combustor` | 単管燃焼器の空気配分、圧損、出口分布、ライナ温度 | `single_can_combustor/README.md` |
| `blade` | 遠心圧縮機の翼形状、子午面、座標とCAD | [Blade Designer](https://github.com/ai-infra-data-lab/centrifugal-blade-designer) |
| `turbine` | 軸流タービンの段、動静翼、排気ディフューザー、STEP | [軸流タービン](https://github.com/ai-infra-data-lab/axial-turbine-designer) |
| `secondary-air` | 冷却、パージ、独立したシール・ベント流路 | `secondary_air_network/README.md` |
| `oil` | 給油配管、圧損、流量とゲージ圧の余裕 | [油配管](https://github.com/ai-infra-data-lab/pipe-pressure-loss-app) |
| `rotor` | 危険速度、離隔、定格の軸受係数と振動応答 | [RotorLab](https://github.com/ai-infra-data-lab/rotorlab) |

Webアプリのポートを変える場合は `start cycle --port 8781` のように指定します。単管燃焼器は既存のCycle Studioを使うか、指定ポートで起動して燃焼器画面を開きます。遠心翼設計、油配管、RotorLabではアプリ別の `C:\CodexAppEnvs` またはアプリ内の `.venv` を検索します。環境に合わせて `GT_SUITE_BLADE_PYTHON`、`GT_SUITE_OIL_PYTHON`、`GT_SUITE_ROTOR_PYTHON` で実行ファイルを指定できます。Cycle Studioから油配管とRotorLabを呼ぶ際の環境設定は設計アプリ連携のCLI節を参照してください。 [設計アプリ連携](DESIGN_TOOLS.md)

## 定格点の計算とレビュー

1. Design Deskで案件と設計版を用意し、機器の段数に応じて評価断面を設定します。全体の番号はCycle Studioで使い、圧縮機・燃焼器・タービンの個別断面は機器内の照合に使います。
2. Cycle Studioで入口条件、燃料、軸回転数と圧力比分配を記録します。信頼できる形状と計算結果を評価断面のIDで結びます。全圧の照合CSVには同じ運転点での絶対全圧を使います。
3. 単管燃焼器の出口条件をタービンへ渡し、3段の翼冷却と段間パージを計算します。給油、機械軸力、継手、ロータの設計点を揃えます。定格通しケースは新しいフォルダーに出力します。
4. Design Deskの「設計成果物」に各ケースの `suite-report.json` と詳細一式を取り込みます。「定格通しケースの比較」と「熱・二次空気・軸系」を開き、運転点の一致、設計版、温度、収束、軸力、給油余裕を確認します。CSVに試験値があるときは出典と測定条件を記録します。 [計算コマンドとCSV形式](DESIGN_TOOLS.md)

## 起動と操作の計測

```powershell
python -m gas_turbine_cycle_app.tools.suite_launcher check --output suite-check.json
python -m gas_turbine_cycle_app.tools.suite_launcher check desk cycle rotor --output suite-check-selected.json
```

共通フォルダーに `start_gas_turbine_suite.ps1` がある構成では、同じ引数でPowerShellスクリプトからも起動できます。

計測は一時DBを使います。WebアプリはHTTP応答が返るまでの時間と代表APIの時間、Qtアプリはオフスクリーンでウィンドウを初期化する時間と計算時間、単管燃焼器はモジュールの読込と計算時間を記録します。初回と再実行ではNASやOSのキャッシュ状態により数値が変わります。詳細な出力例は[実施記録](ROADMAP_EXECUTION.md)から開けます。
