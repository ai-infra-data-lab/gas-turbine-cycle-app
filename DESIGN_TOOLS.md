# 設計アプリ連携

Cycle Studioのヘッダーから「設計アプリ連携」を開く。各機能で「仮想設定を読み込む」を選ぶと、入力のひな形が入る。サイクル入力／ケースJSONを選ぶか、仮想の2段遠心圧縮機サイクルを明示して計算する。保存ケースを使う場合は入力ハッシュを照合し、保存ファイルに含まれる性能結果を入力から再計算する。

## 一つの設計点を受け渡す

1. **インペラーのスラスト・軸締結**を計算する。要素モードの圧縮機を用い、各段の受圧円環、前面・背面の外周圧力、温度、旋回比、軸方向を指定する。継手別の強度ケースを「軸締結強度」で開ける。
2. **単管燃焼器**で空気配分、全圧損失、燃料流量または出口全温を評価する。「通常のサイクル画面で読めるケース」を書き出す。
3. **軸流タービン・排気ディフューザー**にそのケースを入力する。入口全圧・全温・流量・回転数・要求ガス出力を取り込む。段数と仕事配分は `allocations` の各配列で指定する。各案のタービン設計JSONを「軸流タービン設計」で開くと、子午面を編集して再計算できる。
4. **二次空気・サイクルへの反映**で、工程3と同じサイクルケースと「連携結果一式」を指定する。抽気段とタービンの評価断面を `source_stage`・`injections` に指定する。各枝の流量・温度と、Cycle Studioへ一度反映した結果を確認する。
5. **油配管・軸受給油**で、冷間と定格の油温・動粘度、ポンプ差圧曲線、軸受の最低流量・圧力を指定する。作動点の各案を油配管アプリの通常のプロジェクトJSONとして取得できる。
6. **ロータダイナミクス**で、サイクルケースと必要に応じて給油の「連携結果一式」を選ぶ。軸上位置・質量・径方向慣性・極慣性モーメント、回転数別軸受係数を設定する。各案のモデルをRotorLabで開き、Campbell図と同期応答を確認できる。

## 計算境界

- **スラスト**。受圧円環の端はシール半径などに合わせて指定する。同じ受圧面を重複して指定しない。外周絶対静圧の既定は指定したインペラー入口または出口の静圧。等温・一定旋回比の半径方向平衡を円環で面積平均し、前面・背面の差、主流の軸方向圧力作用と運動量を加える。主流面の投影は方向余弦で与える。支持は単一のスラスト軸受とし、継手断面の自由物体図から引張正の内部軸力を渡す。シール漏れは別途計算・測定した外周圧力で反映できる。 [軸力モデルの比較研究](https://oaktrust.library.tamu.edu/items/961d1b78-420b-4974-ac2b-7d8318f1da11)
- **タービン**。定比熱・指定段効率。翼列前後縁の子午面面積から、連続式の低軸流速度側の解を求める。静翼は全圧・全温一定の近似を使う。初段静翼入口の予旋回をゼロとし、後段は前段動翼出口の旋回速度を引き継ぐ。各段の平均半径でEuler仕事を合わせる。排気ディフューザーの面積・全圧損失・静圧変化を記録する。 [圧縮性流量とチョーク](https://www.grc.nasa.gov/www/k-12/airplane/mflchk.html)
- **二次空気**。定格1点の静止圧力境界。抽気元は段出口の絶対全圧と全温、排出先はタービン断面の絶対静圧を使う。冷却枝の入口プレナム全圧、枝出口全温、流量を冷却回路へ渡す。返却対象外の漏れなどがあるケースはネットワーク結果と理由を返す。
- **回転流路・シール**。「回転流路とシールの仮想設定」は、入口の予旋回が周速と一致すると仮定し、指定した全圧残存比から同一回転系の入口相対全状態を求める。ラビリンスの漏れ、絶対全温、回転流路の軸仕事を別々に出力する。静止ネットワークへの直接連成と、回転軸仕事を含むサイクルへの自動反映は別工程。 [静止系・回転系の全エンタルピーの定義](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/cfx_thry/i1298716.html)
- **単管燃焼器**。同形の各缶に均等に分配する。空気配分は指定比率、燃焼はサイクルと同じ燃料と物性、全圧損失は低Machの基準動圧に指定損失係数を掛ける。任意の `thermal` を使うと、指定した出口区画の全温、パターンファクター、ライナ両面の温度と材料温度余裕を表示する。入力と熱回路の仮定は `single_can_combustor/README.md` を参照。
- **給油**。既存の定常管路ソルバーで、表のポンプ差圧と配管の作動点を一致させる。給油圧はゲージ圧。油温ごとの密度・動粘度は入力値。流量は L/min、ポンプ差圧は kPa。 [ポンプ・配管の作動点](https://www.energy.gov/sites/default/files/2014/05/f16/variable_speed_pumping.pdf)
- **ロータ**。部品位置はRotorLabの節点に合わせる。係数表は運転回転数で補間し、その係数を固定して各案の回転数走査を実行する。油温・圧力・流量・余裕は同じ軸受IDで参照する。油条件から軸受の動特性係数を自動推定するには別の軸受モデルが必要。

## 遠心2段のスラスト力感度分析

「設計アプリ連携」で「遠心2段のスラスト力感度分析」を選ぶ。初回は「仮想設定を読み込む」と仮想サイクルを選び、計算する。自分のケースで比較する場合は、圧縮機を2段・要素モードにしたCycle StudioのJSONと受圧・継手条件を指定する。

- `thrust` は通常のスラスト・軸締結と同じ受圧・支持・継手条件です。`stage1_shares` は第1段の対数全圧比分配を0.05〜0.95の比率で指定します。仮想例は0.50と0.75です。入口空気流量を基準ケースの値に固定し、各配分でサイクルを再計算します。
- `sweeps` の `stage` と `cavity` は1から始まります。前面・背面の `side` は `front` または `back` です。外周絶対静圧 `outer_pressure_pa` を省略した基準条件では、その面が参照するインペラー入口または出口の静圧を使います。旋回比は `swirl_ratio`、両面で共有する受圧内半径は `inner_radius_m` です。受圧内半径を変更するときは `side` を省略します。
- `offsets` は基準値への加算量です。例では圧力を±10,000 Pa、旋回比を±0.1、受圧内半径を±0.002 mとし、各点で一つの量だけを変えます。符号や制限値は通常のスラスト計算と同じです。
- 結果表で段別軸力、軸受反力と各配分の基準からの差、スプライン・カービックの軸力を比較します。書き出したJSONには各設定の基準値・適用値、インペラーの静圧上昇、計算元のサイクル入力ハッシュ、軸方向の力の残差が含まれます。圧力や半径の変更量は例示です。感度分析ではシール漏れやクリアランスからキャビティ圧を連成計算しません。

## 3段冷却とシール漏れの連携

- 「3段冷却・段間パージの仮想設定」を読み込むと、静翼・動翼の各3流路と、第2・第3段入口に戻すパージ2流路を計算する。タービンの連携結果から初期静圧を取得し、Cycle Studioの各混合直前の絶対静圧・全温を使って境界条件を更新する。最大30回、境界圧差2 Pa・全温差0.01 K・流量変化1e−7 kg/sを同時に判定する。連携結果には質量・エネルギー収支と反復履歴が残る。
- 「シール漏れ・キャビティ圧と軸スラスト」では、圧縮機断面の絶対静圧と静温を漏れ回路の境界に設定する。各キャビティ外周に対応するノードの静圧と温度を軸力計算に渡し、ラビリンス隙間別の軸受反力と継手荷重を求める。キャビティ内の圧力分布は既存の指定旋回比で評価する。
- タービンとは独立に、軸受室シール・ベントの入力例 `secondary_air_network/examples/bearing_seal_and_vent.json` をSecondary Air Networkの「JSON取込」で開ける。

## 定格の通しケース

ガスタービン設計アプリ一式を展開したフォルダーで実行する。出力先には新しいフォルダー名を指定する。

```powershell
python -m gas_turbine_cycle_app.tools.suite_design_point --output design-point-results
```

遠心2段のサイクルから単管燃焼器、軸流3段、段別冷却、シール・軸力、冷間・定格の給油、定格のロータ応答を計算する。試験評価は同じ設計点に既知の差を加えた仮想CSVで確認する。`--project` にDesign Deskの書出しJSONを渡すと入力元の案件と設計版を記録する。Design Deskの「設計成果物」で `suite-report.json` と `cooling.json` をそれぞれ取り込める。詳細レポートの原本と要約JSONは出力フォルダーごと保管する。

第1段の対数全圧比分配を変えて比較するときは、別の出力先を指定して実行する。

```powershell
python -m gas_turbine_cycle_app.tools.suite_design_point --stage1-share 0.5 --output design-even
python -m gas_turbine_cycle_app.tools.suite_design_point --stage1-share 0.75 --output design-stage1-75
```

両方の出力を詳細JSONと一緒に同じDesign Deskの案件へ取り込み、「定格通しケースの比較」で運転点と結果を照合する。

### 熱・二次空気・軸系の定格レビュー

`support-review.json` には燃焼器の出口分布とライナ温度、段別の冷却注入、各反復の境界ノード・ステーションごとの符号付き差、シール隙間別の軸力と軸締結、給油余裕、定格軸受係数とRotorLabの応答が入る。軸力とロータのサイクル実行ID・入力ハッシュを確認し、ロータの定格回転数と各軸受の油温・流量・圧力を設計点に合わせている。

照合CSVはUTF-8で `measurement_id,unit,value,uncertainty` の4列。測定点と単位は `support-review.json` の `measurement_targets` に記載される。同じ測定点IDは1行までで、不確かさは省略できる。差は計算値からCSVの値を引いた値。試験条件、平均化方法、基準と出典を記録し、`--observation-kind` に `test`、`cfd`、`synthetic` のいずれかを指定する。

```powershell
python -m gas_turbine_cycle_app.tools.suite_design_point --stage1-share 0.5 --observations observations.csv --observation-kind synthetic --observation-reference "仮想定格ケース" --output design-with-observations
```

Design Deskの「設計成果物」で「定格通しケースを取込」を選び、`suite-report.json` と同じフォルダーの詳細JSON一式を指定する。「熱・二次空気・軸系」を展開して「結果を表示」を押すとレビューが開く。

## 評価断面の形状・全圧照合

遠心2段の翼付き6要素プロジェクトJSON、その `.handoff.json`、入力元となった遠心2段設計一式、軸流タービンの連携結果一式と案名を指定する。Blade DesignerのPythonは既存のローカル環境を検索し、必要に応じて `--blade-python` で指定する。

```powershell
python -m gas_turbine_cycle_app.tools.station_review --blade-project equal-blade.json --blade-handoff equal-blade.handoff.json --compressor-design equal-design.json --turbine-report turbine.json --turbine-case "3段均等" --output station-review.json
```

遠心翼列の面積は前・後縁の両壁端点を結ぶ全周回転面の面積で、転向出口は軸方向直線部入口の環状面積。軸流タービンの翼列は子午面の軸方向環状面、排気ディフューザ出口は指定面積比による概略面積を使う。面積はいずれも翼厚による閉塞を差し引く前の値。Cycle Studioの要素全圧は対応する翼列の入口・出口に割り当てる。90°転向とガイドベーンの個別モデル全圧は空欄、軸流静翼のモデル全圧は段モデルの一定全圧の仮定を表示する。

全圧CSVを加えるには `--pressure-csv` と `--pressure-kind test|cfd|synthetic`、`--pressure-reference` を指定する。CSVはUTF-8で、列は `station_code,total_pressure_pa`。対象形状・運転点・平均化条件をそろえた絶対全圧 [Pa] を使い、出典と集計条件を記録する。損失は各静止区間で入口全圧−出口全圧。照合レポートはDesign Deskの設計成果物に取り込める。

## CLI

ワークスペースの親アプリ共通のディレクトリで実行する。入力JSONは `{"cycle": …, "settings": …}`。油配管は `settings` のみ。仮想基準は `"demo_cycle": true` で選べる。設定例は `examples/roadmap/`。二次空気の `settings.turbine` にタービン連携の結果、RotorLabの各案の `oil_supply` に給油条件を渡せる。

```powershell
python -m gas_turbine_cycle_app.design_tools thrust --input request.json --output thrust-report.json
python -m gas_turbine_cycle_app.design_tools combustor --input combustor-request.json --output combustor-report.json
```

コマンド名は `thrust`、`thrust-sensitivity`、`combustor`、`turbine`、`cooling`、`oil`、`rotor`。感度分析の設定は `examples/roadmap/thrust-sensitivity.json` の内容に、`thrust` キーで `examples/roadmap/thrust.json` の内容を加える。画面の仮想設定には両方が入る。油配管はSciPy、RotorLabは既存のROSS環境を使う。Cycle Studioの画面から呼ぶときは、必要に応じて環境変数 `GT_WORKFLOW_OIL_PYTHON`、`GT_WORKFLOW_ROTOR_PYTHON` でPython実行ファイルを指定する。
