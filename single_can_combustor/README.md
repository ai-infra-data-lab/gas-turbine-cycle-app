# 単管燃焼器の設計点計算

各缶の燃料流量、空気配分、希薄完全燃焼の熱収支と全圧損失を計算する独立モジュール。出口の全温・全圧に対応するCycle Studioのケースを出力し、そのケースを軸流タービンへ渡せます。

Cycle Studioの「設計アプリ連携 → 単管燃焼器」、または共通作業フォルダーで `python -m single_can_combustor --input request.json --output result.json` を使用します。 [入力と適用範囲](../gas_turbine_cycle_app/DESIGN_TOOLS.md)
