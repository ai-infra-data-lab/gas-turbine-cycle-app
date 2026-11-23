# 単管燃焼器の設計点計算

各缶の燃料流量、空気配分、希薄完全燃焼の熱収支と全圧損失を計算する独立モジュール。出口の全温・全圧に対応するCycle Studioのケースを出力し、そのケースを軸流タービンへ渡せます。

Cycle Studioの「設計アプリ連携 → 単管燃焼器」、または共通作業フォルダーで `python -m gas_turbine_cycle_app.single_can_combustor --input request.json --output result.json` を使用します。 [入力と適用範囲](../DESIGN_TOOLS.md)

## 出口温度とライナ温度

仮想設定の `thermal` を使うと、出口断面とライナを評価できます。既存の入力ファイルではこの項目を省略できます。 [入力例](../examples/roadmap/combustor.json)

- `exit_sectors` の各行に区画ID、流量比 `mass_fraction`、相対全温偏差 `temperature_offset_k` を入れ、流量比の合計を1にします。温度の共通基準を調整し、区画の質量平均エンタルピーを出口バルク全温のエンタルピーと一致させます。パターンファクターは「最高区画全温−区画の質量平均全温」を「区画の質量平均全温−入口全温」で割った値です。温度偏差は入力として指定します。 [NASAの出口温度分布資料](https://ntrs.nasa.gov/api/citations/19730020186/downloads/19730020186.pdf)
- `liner_segments` に評価区間ごとの面積、主流・冷却側温度、フィルム有効度、熱伝達率、板厚、熱伝導率を入力します。材料温度上限を指定すると余裕が出ます。定常一次元の対流・伝導抵抗で両面温度と熱流束を求めます。ライナの熱流束は局所評価に使用します。燃焼の安定性や詳細な混合分布は個別に評価してください。 [DOEの伝熱ハンドブック](https://www.energy.gov/ehss/articles/doe-hdbk-10122-92)
