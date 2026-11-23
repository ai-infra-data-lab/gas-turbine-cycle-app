"""JSON contract and strict, SI-unit validation. No file I/O."""
import copy
import math

MAX_NODES = 200
MAX_EDGES = 500


class ModelError(ValueError):
    pass


def number(value, label, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelError(f"{label}: 有限の数値を指定してください。")
    if lo is not None and value < lo or hi is not None and value > hi:
        raise ModelError(f"{label}: 範囲 {lo} ～ {hi} を確認してください。")
    return float(value)


def validate(raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ModelError("schema_version=1 のネットワークJSONを指定してください。")
    m = copy.deepcopy(raw)
    if not isinstance(m.get("name"), str) or not m["name"].strip() or len(m["name"]) > 160:
        raise ModelError("案件名を1～160文字で入力してください。")
    g = m.setdefault("gas", {})
    if not isinstance(g, dict):
        raise ModelError("gas はオブジェクトで指定してください。")
    g["R"] = number(g.get("R", 287.05), "気体定数 R", 200, 500)
    g["gamma"] = number(g.get("gamma", 1.4), "比熱比 γ", 1.05, 1.67)
    nodes, edges = m.get("nodes"), m.get("edges")
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= MAX_NODES:
        raise ModelError(f"ノードは2～{MAX_NODES}個で指定してください。")
    if not isinstance(edges, list) or not 1 <= len(edges) <= MAX_EDGES:
        raise ModelError(f"流路は1～{MAX_EDGES}本で指定してください。")
    ids, boundary = set(), set()
    for n in nodes:
        if not isinstance(n, dict):
            raise ModelError("ノードの形式が不正です。")
        key = n.get("id")
        if not isinstance(key, str) or not key or len(key) > 60 or key in ids:
            raise ModelError("ノードIDは重複しない1～60文字の文字列にしてください。")
        ids.add(key)
        n.setdefault("name", key)
        if not isinstance(n["name"], str) or len(n["name"]) > 160:
            raise ModelError(f"{key}: 名称を確認してください。")
        if n.get("kind") not in ("boundary", "junction"):
            raise ModelError(f"{key}: ノード種類はboundary/junctionです。")
        n["pressure_pa"] = number(n.get("pressure_pa", 200000), f"{key} 圧力/初期値 Pa", 1000, 50e6)
        n["temperature_k"] = number(n.get("temperature_k", 500), f"{key} 温度/初期値 K", 150, 2500)
        n["heat_w"] = number(n.get("heat_w", 0), f"{key} 熱入力 W", -1e6, 1e6)
        n.setdefault("frame", "stationary")
        if n["frame"] not in ("stationary", "rotating"):
            raise ModelError(f"{key}: 座標系はstationary/rotatingです。")
        n["rpm"] = number(n.get("rpm", 0), f"{key} 回転数 rpm", -100000, 100000)
        n["radius_m"] = number(n.get("radius_m", 0), f"{key} 半径 m", 0, 2)
        if n["frame"] == "stationary" and n["rpm"] != 0:
            raise ModelError(f"{key}: 静止ノードの回転数は0です。")
        if n["frame"] == "rotating" and n["radius_m"] < .001:
            raise ModelError(f"{key}: 回転ノードの半径は1 mm以上にしてください。")
        if n["kind"] == "boundary":
            boundary.add(key)
            if n["heat_w"] != 0:
                raise ModelError(f"{key}: 圧力温度境界への熱入力は指定できません。")
    if not boundary:
        raise ModelError("圧力・温度境界を指定してください。")
    edgeids, links = set(), {key: set() for key in ids}
    for e in edges:
        if not isinstance(e, dict):
            raise ModelError("流路の形式が不正です。")
        key = e.get("id")
        if not isinstance(key, str) or not key or len(key) > 60 or key in edgeids:
            raise ModelError("流路IDは重複しない1～60文字の文字列にしてください。")
        edgeids.add(key)
        e.setdefault("name", key)
        if not isinstance(e["name"], str) or len(e["name"]) > 160:
            raise ModelError(f"{key}: 名称を確認してください。")
        if e.get("from") not in ids or e.get("to") not in ids or e["from"] == e["to"]:
            raise ModelError(f"{key}: 異なる既存ノードを接続してください。")
        links[e["from"]].add(e["to"])
        links[e["to"]].add(e["from"])
        kind = e.get("kind")
        if kind not in ("orifice", "leak", "pipe", "resistance", "labyrinth", "rotating_pipe"):
            raise ModelError(f"{key}: 未対応の流路種類です。")
        for field in ("source", "notes"):
            e.setdefault(field, "")
            if not isinstance(e[field], str) or len(e[field]) > 4000:
                raise ModelError(f"{key} {field}: 4000文字以内で入力してください。")
        if kind == "labyrinth":
            e["seal_diameter_m"] = number(e.get("seal_diameter_m"), f"{key} 隙間平均径 m", 0.001, 2)
            e["clearance_m"] = number(e.get("clearance_m"), f"{key} 半径隙間 m", 1e-6, min(0.01, e["seal_diameter_m"] * 0.02))
            count = e.get("tooth_count")
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 30:
                raise ModelError(f"{key}: 歯数は1～30の整数です。")
            e["tooth_count"] = count
            clearances = e.get("tooth_clearances_m", [])
            if not isinstance(clearances, list) or len(clearances) not in (0, count):
                raise ModelError(f"{key}: 個別隙間は空配列または歯数と同数で指定してください。")
            e["tooth_clearances_m"] = [number(c, f"{key} 歯{i+1}隙間 m", 1e-6, min(0.01, e["seal_diameter_m"] * 0.02)) for i, c in enumerate(clearances)]
            # area_m2 is a derived nominal area; individual teeth use their own gap.
            e["area_m2"] = math.pi * e["seal_diameter_m"] * e["clearance_m"]
        else:
            e["area_m2"] = number(e.get("area_m2"), f"{key} 面積 m²", 1e-10, 1)
        e["heat_w"] = number(e.get("heat_w", 0), f"{key} 熱入力 W", -1e6, 1e6)
        if kind in ("orifice", "leak", "labyrinth"):
            e["cd"] = number(e.get("cd", 0.7), f"{key} Cd", 0.01, 1)
            e["cd_reverse"] = number(e.get("cd_reverse", e["cd"]), f"{key} 逆流Cd", 0.01, 1)
            if e["heat_w"] != 0:
                raise ModelError(f"{key}: 絞りの熱入力は混合室か配管に設定してください。")
        else:
            e["diameter_m"] = number(e.get("diameter_m"), f"{key} 水力直径 m", 1e-5, 2)
            e["length_m"] = number(e.get("length_m", 0), f"{key} 長さ m", 0, 100)
            e["roughness_m"] = number(e.get("roughness_m", 0), f"{key} 等価砂粒粗さ m", 0, e["diameter_m"] * 0.05)
            e["k"] = number(e.get("k", 0), f"{key} K", 0, 1000)
            e["k_reverse"] = number(e.get("k_reverse", e["k"]), f"{key} 逆流K", 0, 1000)
            e["htc_w_m2k"] = number(e.get("htc_w_m2k", 0), f"{key} 熱伝達率", 0, 10000)
            e["wall_temperature_k"] = number(e.get("wall_temperature_k", 700), f"{key} 壁温 K", 150, 2500)
            if e.get("friction", "auto") not in ("auto", "fixed"):
                raise ModelError(f"{key}: 摩擦指定はauto/fixedです。")
            e.setdefault("friction", "auto")
            e["friction_factor"] = number(e.get("friction_factor", 0.02), f"{key} Darcy係数", 0, 1)
            cells = e.get("cells", 48)
            if isinstance(cells, bool) or not isinstance(cells, int) or not 8 <= cells <= 256:
                raise ModelError(f"{key}: 分割数は8～256の整数です。")
            e["cells"] = cells
            if kind == "resistance" and (e["length_m"] != 0 or e["htc_w_m2k"] != 0 or e["heat_w"] != 0):
                raise ModelError(f"{key}: 局所抵抗は長さ・伝熱率・熱入力を0にしてください。")
            if kind in ("pipe", "rotating_pipe") and e["length_m"] <= 0:
                raise ModelError(f"{key}: 配管長さは0より大きい値です。")
            if kind == "rotating_pipe" and (e["heat_w"] != 0 or e["htc_w_m2k"] != 0):
                raise ModelError(f"{key}: 回転流路は現在は断熱です。熱入力・熱伝達率は0にしてください。")
        byid = {n["id"]:n for n in nodes}
        a,b = byid[e["from"]],byid[e["to"]]
        if a["frame"] != b["frame"] or abs(a["rpm"]-b["rpm"]) > 1e-9:
            raise ModelError(f"{key}: 異なる座標系・回転数の接続は未対応です。相対量をそのまま静止系へ接続できません。")
        if kind == "rotating_pipe":
            if a["frame"] != "rotating":
                raise ModelError(f"{key}: 回転流路の両端は回転ノードにしてください。")
            if e["length_m"] + 1e-12 < abs(b["radius_m"]-a["radius_m"]):
                raise ModelError(f"{key}: 流路長は両端半径差以上です。")
        elif a["frame"] == "rotating" and abs(a["radius_m"]-b["radius_m"]) > 1e-9:
            raise ModelError(f"{key}: 異なる半径の回転ノードは回転流路で接続してください。")
    remaining = set(ids)
    while remaining:
        seen, queue = set(), [next(iter(remaining))]
        while queue:
            key = queue.pop()
            if key not in seen:
                seen.add(key)
                queue.extend(links[key] - seen)
        remaining -= seen
        if len(seen) == 1 or not seen & boundary:
            raise ModelError("孤立ノード、または圧力境界のない接続成分があります。")
    # All pressures are passive: internal pressure must lie inside boundary extrema.
    pmin = min(n["pressure_pa"] for n in nodes if n["id"] in boundary)
    pmax = max(n["pressure_pa"] for n in nodes if n["id"] in boundary)
    active = any(e["kind"] == "rotating_pipe" for e in edges)
    for n in nodes:
        if n["kind"] == "junction":
            if not active:
                n["pressure_pa"] = min(pmax, max(pmin, n["pressure_pa"]))
    if "vortex_study" in m:
        from vortex import validate_vortex
        m["vortex_study"] = validate_vortex(m["vortex_study"])
    return m


def sample():
    """Fictional demonstration, not measured engine data."""
    return {
        "schema_version": 1, "name": "架空サンプル：抽気・冷却・シール分配", "gas": {"R": 287.05, "gamma": 1.4},
        "nodes": [
            {"id": "bleed", "name": "圧縮機抽気（仮定）", "kind": "boundary", "pressure_pa": 600000, "temperature_k": 550},
            {"id": "plenum", "name": "分配室", "kind": "junction", "pressure_pa": 400000, "temperature_k": 550},
            {"id": "cooling", "name": "冷却供給室", "kind": "junction", "pressure_pa": 300000, "temperature_k": 580},
            {"id": "gaspath", "name": "主流排出先（仮定）", "kind": "boundary", "pressure_pa": 180000, "temperature_k": 1000},
            {"id": "sealout", "name": "シール排出先（仮定）", "kind": "boundary", "pressure_pa": 120000, "temperature_k": 700}],
        "edges": [
            {"id": "feed", "name": "抽気供給配管", "from": "bleed", "to": "plenum", "kind": "pipe", "area_m2": 0.0003141592654, "diameter_m": 0.02, "length_m": 0.8, "roughness_m": 0.00001, "k": 0.5, "htc_w_m2k": 30, "wall_temperature_k": 650, "source": "架空の操作説明値。実機未検証。"},
            {"id": "meter", "name": "冷却計量孔", "from": "plenum", "to": "cooling", "kind": "orifice", "area_m2": 0.00012, "cd": 0.72, "source": "仮定Cd。実験による校正が必要。"},
            {"id": "cool", "name": "等価冷却孔群", "from": "cooling", "to": "gaspath", "kind": "orifice", "area_m2": 0.00018, "cd": 0.7, "source": "仮定の合計面積・Cd。翼温評価は含まない。"},
            {"id": "seal", "name": "等価シール漏れ", "from": "plenum", "to": "sealout", "kind": "leak", "area_m2": 0.000035, "cd": 0.65, "source": "仮定の等価絞り。ラビリンス専用モデルではない。"}]}


def stage2_sample():
    m = sample()
    m["name"] = "架空サンプル：4歯ラビリンスと隙間感度"
    m["edges"][-1].update(kind="labyrinth", name="4歯ラビリンス（完全散逸）", seal_diameter_m=0.12,
                            clearance_m=0.00015, tooth_count=4, cd=0.7, cd_reverse=0.7,
                            source="架空値。各歯のCd=0.7を仮定。歯間の軸方向運動エネルギーは完全散逸。実機未校正。")
    m["vortex_study"] = {"kind":"forced", "rpm":30000, "swirl_ratio":0.5,
                         "reference_radius_m":0.08, "start_radius_m":0.08, "end_radius_m":0.18,
                         "reference_pressure_pa":300000, "static_temperature_k":600, "points":21}
    return m


def rotating_sample():
    """One co-rotating network; specified relative inlet/outlet reservoirs."""
    return {"schema_version":1,"name":"架空サンプル：回転半径流路と冷却分配","gas":{"R":287.05,"gamma":1.4},
            "nodes":[
                {"id":"rot_in","name":"回転系供給境界","kind":"boundary","frame":"rotating","rpm":30000,"radius_m":.08,"pressure_pa":400000,"temperature_k":550},
                {"id":"rot_out","name":"外周回転混合室","kind":"junction","frame":"rotating","rpm":30000,"radius_m":.18,"pressure_pa":450000,"temperature_k":675},
                {"id":"cool_out","name":"回転系冷却排出境界","kind":"boundary","frame":"rotating","rpm":30000,"radius_m":.18,"pressure_pa":300000,"temperature_k":675}],
            "edges":[
                {"id":"radial","name":"断熱回転半径流路","kind":"rotating_pipe","from":"rot_in","to":"rot_out","area_m2":.0002,"diameter_m":.0159576912,"length_m":.2,"k":.3,"friction":"fixed","friction_factor":.02,"cells":48,"source":"架空値。完全追従・断熱。摩擦係数は仮定。"},
                {"id":"cooling","name":"回転系の冷却孔","kind":"orifice","from":"rot_out","to":"cool_out","area_m2":.00008,"cd":.7,"source":"架空のCd。両端は同じ半径・回転数。"}]}
