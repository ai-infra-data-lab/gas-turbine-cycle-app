# Cycle Studio 0.1 — 一軸シンプルサイクル

定常・開放シンプルサイクルの設計点計算を行う独立アプリ。Python 3.10以上の標準ライブラリだけで動作し、インストール・インターネット接続・外部CDN・アカウントは不要です。

## GitHubから取得

```powershell
git clone https://github.com/ai-infra-data-lab/gas-turbine-cycle-app.git gas_turbine_cycle_app
cd gas_turbine_cycle_app
python server.py
```

Python 3.10以上が必要です。追加Pythonパッケージのインストールは不要です。公開版に実使用・試験用の保存DBは含みません。

対応する[設計情報管理アプリ](https://github.com/ai-infra-data-lab/gas-turbine-design-manager)は別リポジトリです。現時点では独立して動作し、直接連携は今後実装する構成です。

## 起動

`start.bat` をダブルクリック。ブラウザに `http://127.0.0.1:8780/` が開きます。終了は起動ウィンドウでCtrl+C。別ポートは `python server.py --port 8781`。管理アプリの8770/8772とは別です。

## 機能

- 空気流量から出力を計算、目標正味発電出力から空気流量を逆算。
- 燃料質量の増加、吸気・燃焼器・排気の圧力損失、圧縮機・タービン等エントロピー効率、機械・発電機効率、固定電気補機を考慮。
- 全圧・全温・流量、取出し軸出力、正味発電、LHV熱効率、燃料流量、熱消費率、燃料消費率、質量・エネルギー収支を表示。
- 圧力比の設計点感度解析。基準点の空気流量を固定し、不成立点は理由付きで表示。オフデザイン解析ではない。
- 入力・結果JSON、状態表CSV、保存ケースの読込と比較。入力変更後は結果の保存・書出しを無効化。
- `data/cases.sqlite3` に明示保存。既存アプリ・管理DBは変更しない。

## 計算モデル・操作

画面右上「操作・計算モデル」（`/guide`）に操作、数式、物性・損失の定義、一次資料、サンプル結果を掲載。

比熱一定の理想気体。燃料LHV・空気/ガスのcp/Rを入力し、γ=cp/(cp−R)で計算します。燃料は298.15 Kで顕熱0、空気/ガスも同じ顕熱基準です。燃焼ガス組成や温度依存cpは計算しません。回転数は記録用で性能に影響しません。

既定入力は検討用の仮定です。既定1.2 kg/sの正味発電は約138.723 kW、効率約14.2764%（LHV）。モデル内部の計算値であり、実機との相関は未検証です。管理アプリのサンプルにあった「100 kW」は要求値で、自動的にその出力になる条件ではありません。

再生、再熱、中間冷却、冷却抽気、燃料組成、化学平衡、性能マップ、過渡、軸系マッチング、設計寸法は対象外。負の軸出力では発電機をモーターとして計算せず、発電関連値をnullとします。補機消費で正味出力が0以下の場合は効率等をnullにします。

## 将来の統合

`model.py` はSI入力定義、`engine.py` はUI・DBに依存しない純粋な計算、`server.py` はローカルUIアダプター。`engine.analyze(input)` または `engine.run_case(input)` を他アプリから利用できます。`INTEGRATION.md` を参照。

CLI例：

```powershell
python cli.py --input examples/flow_input.json --output result.json
python cli.py --input examples/target_100kw_input.json --output result-100kw.json
```

`--input` を省くと既定条件を計算します。結果JSONを入力に指定した場合も、入力を取り出して再計算します。

## 保存・検証

全ケースのバックアップは、終了後に `data/cases.sqlite3` をコピー。JSON・DBは暗号化していません。ケースは追加保存で既存の結果を上書きしません。

`python -m unittest -v test_cycle.py` で計算・保存・HTTPを検証します。HTTP接続は実行環境によって許可が必要な場合があります。実施結果・画面確認の範囲は `VERIFICATION.md` を参照。
