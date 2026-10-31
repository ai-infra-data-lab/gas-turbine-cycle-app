"""Independent, auditable measured-versus-cycle evaluation (not a test code rating)."""
import copy
import csv
import hashlib
import io
import json
import math
import statistics
import uuid
from datetime import datetime, timezone

if __package__:
    from .engine import run_case as run_cycle
    from .model import default_input, validate_input
    from .storage import CaseStore
else:
    from engine import run_case as run_cycle
    from model import default_input, validate_input
    from storage import CaseStore

MODULE_ID = 'gt.evaluation.test_comparison'
MODULE_VERSION = '0.1.0'
FORMAT = 'gas-turbine-test-evaluation'
# unit: physical definition, SI multiplier, SI offset
UNITS = {'K': ('total_temperature', 1, 0), 'degC': ('total_temperature', 1, 273.15),
         'Pa': ('absolute_total_pressure', 1, 0), 'kPa': ('absolute_total_pressure', 1000, 0),
         'bar': ('absolute_total_pressure', 100000, 0),
         'kPa(g)': ('absolute_total_pressure', 1000, None),
         'kg/s': ('mass_flow', 1, 0), 'kg/h': ('mass_flow', 1/3600, 0),
         'W': ('power', 1, 0), 'kW': ('power', 1000, 0), 'rpm': ('speed', 1, 0)}
CONDITIONS = {'ambient_temperature_k': ('大気温度', 'total_temperature'),
              'ambient_pressure_pa': ('大気絶対圧', 'absolute_total_pressure'),
              'air_mass_flow_kg_s': ('圧縮機入口全空気流量', 'mass_flow'),
              'shaft_speed_rpm': ('軸回転数', 'speed'),
              'turbine_inlet_temperature_k': ('タービン指定位置の全温', 'total_temperature')}
METRICS = {'air_mass_flow_kg_s': ('入口空気流量', 'mass_flow', 'kg/s'),
           'fuel_mass_flow_kg_s': ('燃料流量', 'mass_flow', 'kg/s'),
           'gas_mass_flow_kg_s': ('排気ガス流量', 'mass_flow', 'kg/s'),
           'compressor_power_w': ('圧縮機吸収動力', 'power', 'W'),
           'turbine_shaft_power_w': ('タービン軸動力', 'power', 'W'),
           'net_shaft_power_w': ('正味軸動力', 'power', 'W'),
           'gross_electric_power_w': ('発電端出力', 'power', 'W'),
           'net_electric_power_w': ('送電端出力（補機差引）', 'power', 'W')}


def number(value, label, low=-1e15, high=1e15):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(label+'は範囲内の有限数値にしてください。')
    return float(value)


def text(value, label, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(label+'の文字列が不正です。')
    return value


def catalog(result):
    """Offer only states actually available; never synthesize static or relative states."""
    out = []
    fields = [('total_temperature_k', '全温', 'total_temperature', 'K'),
              ('total_pressure_pa', '絶対全圧', 'absolute_total_pressure', 'Pa'),
              ('mass_flow_kg_s', '流量', 'mass_flow', 'kg/s')]
    groups = [('station', result['stations'])]
    for machine, train in result['stage_results'].items():
        groups.append((machine, train.get('states', [])))
    for group, states in groups:
        for index, state in enumerate(states):
            for field, label, definition, unit in fields:
                if field not in state:
                    continue
                out.append({'key': f'{group}:{index}:{field}',
                            'label': f'{group} / {state.get("name", state.get("id",index))} / {label}',
                            'definition': definition, 'unit': unit, 'value': state[field]})
    for key, (label, definition, unit) in METRICS.items():
        out.append({'key': 'metric:'+key, 'label': label, 'definition': definition, 'unit': unit,
                    'value': result['metrics'][key]})
    return out


def parse_csv(source, time_column):
    text(source, 'CSV', 600000)
    try:
        reader = csv.reader(io.StringIO(source.lstrip('\ufeff')), strict=True)
        headers = next(reader)
        if not headers or len(headers) > 100 or len(set(headers)) != len(headers) or any(not h.strip() for h in headers):
            raise ValueError('CSV列名は空欄・重複なしで100列以内にしてください。')
        if time_column not in headers:
            raise ValueError('時刻列がありません。時刻は試験開始からの秒数で指定してください。')
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError('CSVの列数が一致しません。')
            if len(rows) >= 10000:
                raise ValueError('CSVは10000行以内にしてください。')
            record = dict(zip(headers, row))
            try:
                t = float(record[time_column])
            except ValueError:
                raise ValueError('時刻列は欠損のない秒数にしてください。') from None
            if not math.isfinite(t) or rows and t <= rows[-1][0]:
                raise ValueError('時刻は有限数・厳密な昇順にしてください。')
            rows.append((t, record))
    except (StopIteration, csv.Error) as exc:
        raise ValueError('CSVを読み取れません。ヘッダー付きCSVを指定してください。') from exc
    if not rows:
        raise ValueError('CSVにデータ行がありません。')
    return headers, rows


def validate(raw):
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw.get('schema_version') != 1:
        raise ValueError('試験評価のschema_version=1を指定してください。')
    v = copy.deepcopy(raw)
    v['name'] = text(v.get('name'), '評価名', 150).strip()
    if not v['name']:
        raise ValueError('評価名を入力してください。')
    for key in ('source_name', 'notes', 'calibration_note', 'baseline_source'):
        v[key] = text(v.get(key, ''), key)
    v['baseline_input'] = validate_input(v.get('baseline_input'))
    # Hold flow fixed to measured or baseline flow; never solve it from validation power.
    if v['baseline_input']['mode'] != 'airflow':
        raise ValueError('試験評価の基準ケースは空気流量→出力モードにしてください。')
    if type(v.get('calibrated_model')) is not bool:
        raise ValueError('校正モデルの指定が必要です。')
    v['time_column'] = text(v.get('time_column'), '時刻列', 100)
    headers, rows = parse_csv(v.get('csv_text'), v['time_column'])
    v['start_s'] = number(v.get('start_s'), '区間開始秒')
    v['end_s'] = number(v.get('end_s'), '区間終了秒', v['start_s'])
    if not any(v['start_s'] <= t <= v['end_s'] for t, _ in rows):
        raise ValueError('指定区間にデータがありません。')
    channels = v.get('channels')
    if not isinstance(channels, list) or not 1 <= len(channels) <= 60:
        raise ValueError('計測項目は1〜60個にしてください。')
    columns, bindings = set(), set()
    for ch in channels:
        if not isinstance(ch, dict) or ch.get('column') not in headers or ch['column'] == v['time_column'] or ch['column'] in columns:
            raise ValueError('計測列は時刻以外の重複しないCSV列を指定してください。')
        columns.add(ch['column'])
        if ch.get('role') not in ('condition', 'calibration', 'validation') or ch.get('unit') not in UNITS:
            raise ValueError('用途・単位を指定してください。')
        if ch.get('definition') != UNITS[ch['unit']][0]:
            raise ValueError('計測の定義と単位の組合せが一致しません。静圧・静温・相対全温は直接比較できません。')
        for key in ('instrument', 'calibration_date', 'location', 'uncertainty_basis'):
            ch[key] = text(ch.get(key, ''), key)
        if not ch['location'].strip():
            raise ValueError(ch['column']+'の計測位置・混合前後を記録してください。')
        for key in ('measurement_u', 'model_u', 'absolute_tolerance', 'relative_tolerance_pct'):
            if ch.get(key) is not None:
                ch[key] = number(ch[key], key, 0, 1e12)
            else:
                ch[key] = None
        if ch['unit'] == 'kPa(g)':
            ch['gauge_reference_pa'] = number(ch.get('gauge_reference_pa'), 'ゲージ基準絶対圧 Pa', 1, 1e7)
        if ch['role'] == 'condition':
            key = ch.get('parameter')
            if key not in CONDITIONS or CONDITIONS[key][1] != ch['definition'] or key in bindings:
                raise ValueError('運転条件の対象パラメータ・定義が不正または重複しています。')
            bindings.add(key)
    if v['calibrated_model'] and not v['calibration_note'].strip():
        raise ValueError('校正データと検証データの関係を校正メモに記録してください。')
    return v


def summarize(v):
    _, rows = parse_csv(v['csv_text'], v['time_column'])
    selected = [(t, row) for t, row in rows if v['start_s'] <= t <= v['end_s']]
    stats = []
    for ch in v['channels']:
        _, scale, offset = UNITS[ch['unit']]
        if offset is None:
            offset = ch['gauge_reference_pa']
        values, times = [], []
        for t, row in selected:
            try:
                x = float(row[ch['column']]) * scale + offset
            except ValueError:
                continue
            if math.isfinite(x):
                values.append(x); times.append(t)
        if not values:
            raise ValueError(ch['column']+'に有効な計測値がありません。')
        # Avoid physically invalid pressure / absolute temperatures being averaged away.
        if ch['definition'] in ('total_temperature', 'absolute_total_pressure') and min(values) <= 0:
            raise ValueError(ch['column']+'の絶対温度・絶対圧は正にしてください。')
        mean = statistics.mean(values)
        slope = None
        if len(values) > 1:
            tm = statistics.mean(times)
            slope = sum((t-tm)*(x-mean) for t,x in zip(times,values))/sum((t-tm)**2 for t in times)
        stats.append({'column': ch['column'], 'n': len(values), 'missing_or_invalid': len(selected)-len(values),
                      'mean_si': mean, 'stddev_si': statistics.stdev(values) if len(values)>1 else None,
                      'minimum_si': min(values), 'maximum_si': max(values), 'slope_si_per_s': slope,
                      'scale': scale})
    return stats, len(rows), len(selected)


def run_case(raw):
    v = validate(raw)
    stats, total, selected = summarize(v)
    conditions = copy.deepcopy(v['baseline_input'])
    used = []
    for ch, st in zip(v['channels'], stats):
        if ch['role'] == 'condition':
            key = ch['parameter']; conditions['parameters'][key] = st['mean_si']; used.append(key)
    cycle = run_cycle(conditions)  # Never trust imported or browser-supplied results.
    targets = {x['key']: x for x in catalog(cycle['result'])}
    warnings = ['定常区間は利用者の指定です。標準偏差・範囲・傾向を確認してください。自動定常判定は行いません。',
                '運転条件は平均値に置換した設計点計算です。性能マップによるオフデザイン解析・基準状態補正ではありません。',
                '計測とモデルの拡張不確かさUは同じ包含係数k=2・独立を仮定します。計測入力の感度伝播や相関は未計算です。']
    contaminated = v['calibrated_model'] or any(c['role']=='calibration' for c in v['channels'])
    # Known direct constraints are explicitly excluded, including duplicate sensor columns.
    direct = {'ambient_temperature_k': {'station:0:total_temperature_k', 'station:1:total_temperature_k'},
              'ambient_pressure_pa': {'station:0:total_pressure_pa','station:5:total_pressure_pa'},
              'air_mass_flow_kg_s': {'metric:air_mass_flow_kg_s','station:0:mass_flow_kg_s','station:1:mass_flow_kg_s'},
              'turbine_inlet_temperature_k': {'station:3:total_temperature_k'}}
    constrained = set().union(*(direct.get(k,set()) for k in used))
    if 'air_mass_flow_kg_s' in used:
        constrained.update(k for k in targets if k.startswith('compressor:') and k.endswith(':mass_flow_kg_s'))
    for key in used:
        # Also catch aliases at train boundaries and a prescribed first-rotor inlet.
        definition = CONDITIONS[key][1]
        fixed_value = conditions['parameters'][key]
        constrained.update(k for k,t in targets.items() if t['definition']==definition and t['value'] is not None
                           and math.isclose(t['value'],fixed_value,rel_tol=1e-10,abs_tol=1e-9))
    if contaminated:
        warnings.append('校正データを使用したモデルです。この評価内の全項目は独立検証として扱わず参考差分を表示します。独立した別試験で評価してください。')
    comparisons = []
    for ch, st in zip(v['channels'], stats):
        row = {**st, 'role': ch['role'], 'unit': ch['unit'], 'definition': ch['definition'],
               'target': ch.get('target'), 'independent': False}
        if ch['role'] == 'condition':
            row.update({'label': CONDITIONS[ch['parameter']][0], 'si_unit': {'total_temperature':'K','absolute_total_pressure':'Pa','mass_flow':'kg/s','speed':'rpm'}[ch['definition']], 'status': 'condition', 'parameter': ch['parameter']})
        else:
            target = targets.get(ch.get('target'))
            if target is None or target['definition'] != ch['definition']:
                raise ValueError(ch['column']+'の計算位置と計測定義が一致しません。')
            predicted = target['value']
            row.update({'label':target['label'], 'predicted_si':predicted, 'si_unit':target['unit']})
            independent = ch['role']=='validation' and not contaminated and ch['target'] not in constrained
            row['independent'] = independent
            if predicted is None:
                row['status'] = 'undefined_prediction'
            else:
                delta = st['mean_si'] - predicted
                scale = st['scale']
                mu = ch['measurement_u']*scale if ch['measurement_u'] is not None else None
                pu = ch['model_u']*scale if ch['model_u'] is not None else None
                combined = math.hypot(mu,pu) if mu is not None and pu is not None else None
                tol = None
                if ch['absolute_tolerance'] is not None or ch['relative_tolerance_pct'] is not None:
                    tol = max((ch['absolute_tolerance'] or 0)*scale, abs(predicted)*(ch['relative_tolerance_pct'] or 0)/100)
                row.update({'difference_si': delta, 'difference_pct': delta/predicted*100 if predicted else None,
                            'measurement_u_si':mu,'model_u_si':pu,'combined_u_si':combined,
                            'normalized_difference':delta/combined if combined else None,
                            'tolerance_si':tol,'within_tolerance':abs(delta)<=tol if tol is not None else None,
                            'within_uncertainty':abs(delta)<=combined if combined is not None else None,
                            'status': 'independent_comparison' if independent else 'reference_only'})
        comparisons.append(row)
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    digest = lambda obj: hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'format':FORMAT,'schema_version':1,'module_id':MODULE_ID,'module_version':MODULE_VERSION,
            'run_id':str(uuid.uuid4()),'completed_at':now,'input_hash':digest(v), 'input':v,
            'result':{'status':'evaluated','source_sha256':hashlib.sha256(v['csv_text'].encode()).hexdigest(),
                      'total_rows':total,'selected_rows':selected,'applied_parameters':used,
                      'comparisons':comparisons,'cycle_case':cycle,'warnings':warnings}}


def read_input_file(document):
    if isinstance(document,dict) and document.get('format')==FORMAT:
        if document.get('module_id')!=MODULE_ID or document.get('module_version')!=MODULE_VERSION:
            raise ValueError('対応した試験評価JSONを選択してください。')
        document = document.get('input')
    return validate(document)


class EvaluationStore(CaseStore):
    def list(self):
        with self.connect() as db:
            return [{'id':r['id'],'created_at':r['created_at'],'name':json.loads(r['document'])['input']['name']}
                    for r in db.execute('SELECT * FROM cases ORDER BY rowid DESC')]


def example_input():
    base = default_input()
    out = {'schema_version':1,'name':'仮想試験・定常60〜100秒','source_name':'fictional-test.csv',
           'notes':'すべて仮想計測値。検証方法の操作例です。','baseline_source':'既定NASA9設計点',
           'calibrated_model':False,'calibration_note':'','baseline_input':base,
           'time_column':'time_s','start_s':60,'end_s':100,'channels':[]}
    specs = [('ambient_C','condition','degC','ambient_temperature_k',None,0.5),
             ('ambient_kPa','condition','kPa','ambient_pressure_pa',None,.1),
             ('air_kg_s','condition','kg/s','air_mass_flow_kg_s',None,.01),
             ('T2_C','validation','degC',None,'station:2:total_temperature_k',2),
             ('P2_kPa','validation','kPa',None,'station:2:total_pressure_pa',2),
             ('T4_C','validation','degC',None,'station:4:total_temperature_k',4),
             ('fuel_kg_h','validation','kg/h',None,'metric:fuel_mass_flow_kg_s',.4),
             ('electric_kW','validation','kW',None,'metric:net_electric_power_w',2)]
    for col,role,unit,param,target,u in specs:
        out['channels'].append({'column':col,'role':role,'unit':unit,'definition':UNITS[unit][0],
                                'parameter':param,'target':target,'measurement_u':u,'model_u':u,
                                'absolute_tolerance':u*2,'relative_tolerance_pct':1,
                                'instrument':'仮想計器 '+col,'calibration_date':'仮想・未校正',
                                'location':col+' / 定義を確認済み（全温・全圧・軸流量）',
                                'uncertainty_basis':'操作例の仮定値、平均の拡張不確かさ k=2',
                                'gauge_reference_pa':101325})
    result=run_cycle(base)['result']; targets={x['key']:x['value'] for x in catalog(result)}
    stream=io.StringIO(); writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(['time_s']+[s[0] for s in specs])
    for t,jitter in ((0,-20),(20,-10),(40,-4),(60,-1),(70,.5),(80,0),(90,-.5),(100,1),(120,10)):
        values=[15,101.325,1.2,targets['station:2:total_temperature_k']-273.15+3+jitter,
                targets['station:2:total_pressure_pa']/1000-1+jitter*.1,
                targets['station:4:total_temperature_k']-273.15+8+jitter,
                targets['metric:fuel_mass_flow_kg_s']*3600+.2+jitter*.03,
                targets['metric:net_electric_power_w']/1000-2+jitter*.1]
        if t==80: values[6]=''  # one deliberately missing fuel measurement
        writer.writerow([t]+values)
    out['csv_text']=stream.getvalue()
    return out
