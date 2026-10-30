"""Independent preliminary static shaft-joint screening, SI contract.

Uniform tooth strips, supplied load sharing/allowables; not AGMA/Gleason rating.
Curvic uses a symmetric frictionless flank-normal wedge and an annular opening
surrogate. Contact pressure is nominal average, never Hertzian contact stress.
"""
import copy
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone

MODULE_ID='gt.mechanical.shaft_joint'
MODULE_VERSION='0.1.0'

# key, Japanese label, displayed unit, UI multiplier, SI lower/upper/default
LOAD=[('torque_n_m','伝達トルク','N m',1,-1e9,1e9,500.),
      ('power_w','伝達動力','kW',.001,-1e10,1e10,314159.2653589793),
      ('speed_rpm','軸回転数','rpm',1,1,300000,6000.),
      ('axial_force_n','軸方向分離荷重','kN',.001,0,1e9,1000.),
      ('bending_moment_n_m','曲げモーメント','N m',1,0,1e9,50.),
      ('service_factor','荷重の使用係数','—',1,1,10,1.5)]
FACTORS=[('engaged_fraction','荷重を分担する歯の割合','—',1,.01,1,.5),
         ('face_load_factor','歯幅方向の最大荷重係数','—',1,1,10,1.2),
         ('required_safety','応力の要求安全率','—',1,1,10,1.5),
         ('shaft_k_bending','軸の軸力・曲げ応力集中係数','—',1,1,10,1.5),
         ('shaft_k_torsion','軸のねじり応力集中係数','—',1,1,10,1.3)]
MATERIAL=[('temperature_k','許容応力を評価した使用温度','K',1,100,2000,500.),
          ('contact_allowable_pa','両部材の弱い側の平均歯面圧許容値','MPa',1e-6,1e5,5e9,250e6),
          ('bending_allowable_pa','両部材の弱い側の歯元曲げ許容値','MPa',1e-6,1e5,5e9,300e6),
          ('shear_allowable_pa','両部材の弱い側の歯元せん断許容値','MPa',1e-6,1e5,5e9,180e6),
          ('root_equivalent_allowable_pa','歯元相当応力の許容値','MPa',1e-6,1e5,5e9,350e6),
          ('shaft_equivalent_allowable_pa','軸断面の相当応力許容値','MPa',1e-6,1e5,5e9,350e6),
          ('hub_hoop_allowable_pa','ハブ周応力の許容値','MPa',1e-6,1e5,5e9,250e6)]
BASE_GEOMETRY=[('tooth_count','歯数','歯',1,4,300,24),
               ('root_thickness_a_m','部材Aの歯元厚さ','mm',1000,.0001,.2,.003),
               ('root_thickness_b_m','部材Bの歯元厚さ','mm',1000,.0001,.2,.0035),
               ('lever_arm_a_m','部材Aの接線荷重作用高さ','mm',1000,.00001,.1,.0015),
               ('lever_arm_b_m','部材Bの接線荷重作用高さ','mm',1000,.00001,.1,.0015),
               ('shaft_outer_m','軸危険断面外径','mm',1000,.001,2,.05),
               ('shaft_inner_m','軸危険断面内径','mm',1000,0,2,.02)]
SPLINE=[('pitch_diameter_m','荷重作用径','mm',1000,.001,2,.06),
        ('engagement_length_m','有効かみ合い長さ','mm',1000,.0001,2,.03),
        ('engagement_height_m','有効歯丈（半径方向投影）','mm',1000,.00001,.1,.002),
        ('pressure_angle_deg','歯面の有効圧力角','deg',1,0,45,30.),
        ('hub_inner_m','ハブ歯底内径（等価圧力作用径）','mm',1000,.001,2,.066),
        ('hub_outer_m','ハブ外径','mm',1000,.001,3,.1)]
CURVIC=[('outer_diameter_m','歯環外径','mm',1000,.001,2,.1),
        ('inner_diameter_m','歯環内径','mm',1000,.001,2,.07),
        ('flank_height_m','有効歯面高さ（実歯面上）','mm',1000,.00001,.1,.004),
        ('pressure_angle_deg','歯面法線の接線面からの角度','deg',1,5,45,30.)]
CLAMP=[('preload_n','組立締付力（ボルト群合計）','kN',.001,1,1e9,80000.),
       ('scatter_fraction','締付力のばらつき幅','—',1,0,.8,.1),
       ('relaxation_fraction','最小締付力の緩和率','—',1,0,.9,.1),
       ('thermal_change_n','熱による締付力変化（増加が正）','kN',.001,-1e9,1e9,-5000.),
       ('separation_factor','分離力に対する要求締付倍率','—',1,1,3,1.75),
       ('bolt_tensile_area_m2','ボルト群合計の有効引張面積','mm²',1e6,1e-6,.1,.00025),
       ('bolt_load_fraction','外力のボルト群負担割合','—',1,0,1,.2),
       ('bolt_allowable_pa','使用温度でのボルト引張許容値','MPa',1e-6,1e5,5e9,700e6)]

REFERENCES=[
 {'title':'NASA SP-8101, Liquid Rocket Engine Turbopump Shafts and Couplings, §§3.3.1–3.3.2',
  'url':'https://ntrs.nasa.gov/api/citations/19740006328/downloads/19740006328.pdf',
  'use':'Spline tooth-load sharing, curvic separating forces/preload and need for detailed final stress analysis. Not a reproduced rating formula.'},
 {'title':'AGMA 945-1-B20, Splines – Design and Application (scope only)',
  'url':'https://members.agma.org/MyAGMA/MyAGMA/Store/Item_Detail.aspx?Category=STANDARDS&iProductCode=945_1_B20',
  'use':'Lists compressive/shear/bending/hoop rating scope. Full paid equations are not implemented; no standards-compliance claim.'}]


def schema(kind):
    if kind not in ('spline','curvic'): raise ValueError('締結形式はsplineまたはcurvicを選択してください。')
    return {'load':LOAD,'geometry':BASE_GEOMETRY+(SPLINE if kind=='spline' else CURVIC),
            'factors':FACTORS,'material':MATERIAL,**({'clamp':CLAMP} if kind=='curvic' else {})}


def default_input(kind='spline'):
    fields=schema(kind)
    result={'schema_version':1,'kind':kind,'name':('スプライン' if kind=='spline' else 'カービック')+'・仮定入力サンプル',
            'context':{'project_id':None,'component_id':None,'source_revision':None},
            **{group:{f[0]:f[6] for f in items} for group,items in fields.items()}}
    result['load'].update(mode='torque',source_note='操作説明用の仮定荷重。実機未検証。')
    result['material']['basis']='操作説明用の仮定許容値。材質・熱処理・温度・寿命に対する適用は未検証。'
    if kind=='curvic':
        result['geometry'].update(root_thickness_a_m=.005,root_thickness_b_m=.0055,lever_arm_a_m=.002,lever_arm_b_m=.002)
    return result


def validate_input(raw):
    if not isinstance(raw,dict) or type(raw.get('schema_version')) is not int or raw['schema_version']!=1:
        raise ValueError('軸締結入力のschema_versionは1を指定してください。')
    fields=schema(raw.get('kind'))
    if set(raw)!=set(fields)|{'schema_version','kind','name','context'}: raise ValueError('軸締結入力に不足または未知の項目があります。')
    def string(value,label,maximum):
        if not isinstance(value,str) or not 1<=len(value.strip())<=maximum: raise ValueError(label+'の文字数が不正です。')
        return value.strip()
    out={'schema_version':1,'kind':raw['kind'],'name':string(raw['name'],'ケース名',150)}
    for group,items in fields.items():
        special={'mode','source_note'} if group=='load' else {'basis'} if group=='material' else set()
        values=raw[group]
        if not isinstance(values,dict) or set(values)!={f[0] for f in items}|special: raise ValueError(group+'の項目が不正です。')
        out[group]={}
        for key,label,unit,factor,low,high,_ in items:
            x=values[key]
            if type(x) not in (int,float) or not math.isfinite(x) or not low<=x<=high: raise ValueError(f'{label}は{low*factor:g}〜{high*factor:g} {unit}の有限数で指定してください。')
            if key=='tooth_count' and (type(x) is not int): raise ValueError('歯数は整数で指定してください。')
            out[group][key]=int(x) if key=='tooth_count' else float(x)
        if group=='load':
            if values['mode'] not in ('torque','power'): raise ValueError('荷重指定はtorqueまたはpowerです。')
            out[group].update(mode=values['mode'],source_note=string(values['source_note'],'荷重の根拠',2000))
        if group=='material': out[group]['basis']=string(values['basis'],'許容値の根拠',2000)
    ctx=raw['context']
    if not isinstance(ctx,dict) or set(ctx)!={'project_id','component_id','source_revision'}: raise ValueError('contextの形式が不正です。')
    out['context']=copy.deepcopy(ctx)
    for key in ('project_id','component_id'):
        if ctx[key] is not None:
            try: out['context'][key]=str(uuid.UUID(ctx[key]))
            except (ValueError,TypeError,AttributeError): raise ValueError('contextのIDはUUIDまたはnullです。') from None
    rev=ctx['source_revision']
    if rev is not None and (type(rev) is not int or rev<1): raise ValueError('リビジョンは正の整数またはnullです。')
    if (ctx['component_id'] is not None or rev is not None) and ctx['project_id'] is None: raise ValueError('部品・リビジョンには案件IDが必要です。')
    g=out['geometry']
    if g['shaft_inner_m']>=g['shaft_outer_m']: raise ValueError('軸内径は軸外径より小さくしてください。')
    if out['kind']=='spline':
        if not g['shaft_outer_m']<g['pitch_diameter_m']<g['hub_inner_m']<g['hub_outer_m']: raise ValueError('軸危険断面外径 < 荷重作用径 < ハブ歯底内径 < ハブ外径としてください。')
        width=g['engagement_length_m']; pitch=math.pi*g['pitch_diameter_m']/g['tooth_count']
    else:
        if g['inner_diameter_m']>=g['outer_diameter_m']: raise ValueError('歯環内径は歯環外径より小さくしてください。')
        if g['shaft_outer_m']>g['outer_diameter_m']: raise ValueError('軸危険断面外径は歯環外径以下にしてください。')
        width=(g['outer_diameter_m']-g['inner_diameter_m'])/2
        pitch=math.pi*(g['outer_diameter_m']+g['inner_diameter_m'])/2/g['tooth_count']
    if max(g['root_thickness_a_m'],g['root_thickness_b_m'])>=pitch: raise ValueError('歯元厚さは作用径上の円周ピッチ未満にしてください。')
    if width<=0: raise ValueError('有効歯幅が0以下です。')
    return out


def input_hash(inp):
    return hashlib.sha256(json.dumps(validate_input(inp),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def nominal_torque(inp):
    load=inp['load']
    return abs(load['torque_n_m'] if load['mode']=='torque' else load['power_w']/(load['speed_rpm']*2*math.pi/60))


def _evaluate(inp,torque):
    g=inp['geometry']; m=inp['material']; f=inp['factors']; load=inp['load']
    k=load['service_factor']; td=torque*k; fa=load['axial_force_n']*k; moment=load['bending_moment_n_m']*k
    ne=g['tooth_count']*f['engaged_fraction']; angle=math.radians(g['pressure_angle_deg'])
    r=g['pitch_diameter_m']/2 if inp['kind']=='spline' else (g['outer_diameter_m']+g['inner_diameter_m'])/4
    width=g['engagement_length_m'] if inp['kind']=='spline' else (g['outer_diameter_m']-g['inner_diameter_m'])/2
    ft=td/r; tooth=ft/ne*f['face_load_factor']; rows=[]; extra={}
    def check(key,label,demand,limit,unit='Pa',safety=None):
        sf=f['required_safety'] if safety is None else safety
        utilization=demand*sf/limit if limit>0 else (0. if demand==0 else None)
        passed=utilization is not None and utilization<=1+1e-12
        rows.append(dict(key=key,label=label,demand=demand,allowable=limit,unit=unit,required_safety=sf,
                         safety_factor=limit/demand if demand>0 else None,margin=1/utilization-1 if utilization and utilization>0 else None,
                         utilization=utilization,passed=passed))
    shaft_axial=fa
    if inp['kind']=='spline':
        bearing=ft*f['face_load_factor']/(ne*width*g['engagement_height_m'])
        radial=ft*math.tan(angle)
        pi=radial/(math.pi*g['hub_inner_m']*width)
        ri=g['hub_inner_m']/2; ro=g['hub_outer_m']/2
        hoop=pi*(ro**2+ri**2)/(ro**2-ri**2)
        check('hub_hoop','ハブ内面の等価周応力',hoop,m['hub_hoop_allowable_pa'])
        axial_root=0.
        extra.update(radial_force_magnitude_sum_n=radial,hub_equivalent_internal_pressure_pa=pi)
    else:
        c=inp['clamp']; ri=g['inner_diameter_m']/2; ro=g['outer_diameter_m']/2
        area=math.pi*(ro**2-ri**2); inertia=math.pi*(ro**4-ri**4)/4
        min_pre=max(0.,c['preload_n']*(1-c['scatter_fraction'])*(1-c['relaxation_fraction'])+c['thermal_change_n'])
        max_pre=max(0.,c['preload_n']*(1+c['scatter_fraction'])+c['thermal_change_n'])
        sep=ft*math.tan(angle); bending_equivalent=moment*area*ro/inertia
        separating=sep+fa+bending_equivalent
        required=c['separation_factor']*separating
        check('positive_clamp','正の残存締付力',1.,min_pre,'N',1.)
        check('clamp_reserve','残存締付力と要求締付力',required,min_pre,'N',1.)
        peak_clamp=max_pre+bending_equivalent
        nplus=(peak_clamp/math.sin(angle)+ft/math.cos(angle))/2
        nminus=(min_pre-fa-bending_equivalent)/math.sin(angle)/2-ft/math.cos(angle)/2
        bearing=nplus*f['face_load_factor']/(ne*width*g['flank_height_m'])
        axial_root=peak_clamp/ne*f['face_load_factor']
        bolt=(max_pre+c['bolt_load_fraction']*separating)/c['bolt_tensile_area_m2']
        check('bolt_tension','ボルト群の平均引張応力',bolt,c['bolt_allowable_pa'])
        shaft_axial+=max_pre
        extra.update(separating_force_torque_n=sep,separating_force_total_n=separating,
                     bending_opening_equivalent_force_n=bending_equivalent,retained_preload_min_n=min_pre,assembly_preload_max_n=max_pre,
                     required_retained_preload_n=required,minimum_annular_pressure_pa=(min_pre-fa-sep)/area-moment*ro/inertia,
                     unloaded_flank_normal_force_n=nminus,flank_lift_predicted=nminus<0,
                     contact_model_valid=min_pre>0 and min_pre>=separating,annular_area_m2=area,annular_second_moment_m4=inertia,
                     minimum_assembly_preload_n=max(0.,(required-c['thermal_change_n'])/((1-c['scatter_fraction'])*(1-c['relaxation_fraction']))))
    check('bearing','平均歯面圧（局所接触応力ではない）',bearing,m['contact_allowable_pa'])
    roots=[]
    for side in ('a','b'):
        t=g[f'root_thickness_{side}_m']; arm=g[f'lever_arm_{side}_m']
        shear=tooth/(width*t); bending=6*tooth*arm/(width*t*t); direct=axial_root/(width*t)
        equivalent=math.sqrt((bending+direct)**2+3*shear**2)
        check('root_bending_'+side,'部材'+side.upper()+' 歯元曲げ',bending,m['bending_allowable_pa'])
        check('root_shear_'+side,'部材'+side.upper()+' 歯元せん断',shear,m['shear_allowable_pa'])
        check('root_equivalent_'+side,'部材'+side.upper()+' 歯元相当応力',equivalent,m['root_equivalent_allowable_pa'])
        roots.append(dict(member=side,bending_pa=bending,shear_pa=shear,axial_compression_pa=direct,equivalent_pa=equivalent))
    ds=g['shaft_outer_m']; di=g['shaft_inner_m']; area_s=math.pi*(ds**2-di**2)/4
    bending_s=32*moment*ds/(math.pi*(ds**4-di**4)); torsion_s=16*td*ds/(math.pi*(ds**4-di**4))
    normal=(shaft_axial/area_s+bending_s)*f['shaft_k_bending']; shear_s=torsion_s*f['shaft_k_torsion']
    equiv=math.sqrt(normal**2+3*shear_s**2)
    check('shaft_equivalent','軸危険断面の相当応力',equiv,m['shaft_equivalent_allowable_pa'])
    worst=max(rows,key=lambda x: x['utilization'] if x['utilization'] is not None else math.inf)
    return dict(status='within_preliminary_limits' if all(x['passed'] for x in rows) else 'exceeds_preliminary_limits',
                checks=rows,governing_check=worst['key'],maximum_utilization=worst['utilization'],
                nominal_torque_n_m=torque,design_torque_n_m=td,tangential_force_n=ft,effective_tooth_count=ne,
                tooth_peak_tangential_force_n=tooth,mean_load_radius_m=r,effective_face_width_m=width,roots=roots,
                shaft_normal_stress_pa=normal,shaft_shear_stress_pa=shear_s,**extra)


def analyze(raw):
    inp=validate_input(raw); torque=nominal_torque(inp); r=_evaluate(inp,torque)
    baseline=_evaluate(inp,0.)
    if baseline['status']!='within_preliminary_limits':
        capacity=0.; lower=None; reason='zero_torque_already_exceeds_limits'
    else:
        lo,hi=0.,max(torque,1.)
        while hi<1e12 and _evaluate(inp,hi)['status']=='within_preliminary_limits': lo=hi; hi=min(hi*2,1e12)
        if _evaluate(inp,hi)['status']=='within_preliminary_limits': capacity=None; lower=hi; reason='search_upper_bound_reached'
        else:
            for _ in range(55):
                mid=(lo+hi)/2
                if _evaluate(inp,mid)['status']=='within_preliminary_limits': lo=mid
                else: hi=mid
            capacity=lo; lower=None; reason='fixed_geometry_and_other_loads'
    r.update(allowable_nominal_torque_n_m=capacity,capacity_lower_bound_n_m=lower,capacity_basis=reason,
             module_id=MODULE_ID,module_version=MODULE_VERSION,
             qualifications=['概略静強度評価。規格準拠・実機安全・寿命を保証する判定ではありません。',
                             '平均歯面圧と矩形歯の片持ち梁近似。Hertz接触、曲面接触、歯元フィレット、歯当たり、フレッティング、疲労、クリープ、回転遠心応力、熱応力は未評価。',
                             '許容応力は指定温度で入力した弱い部材側の値を使用。材質・熱処理・寿命から自動設定しません。',
                             '許容トルクは現在の軸力・曲げ・締付条件を固定した値。動力からのトルクは選択した軸位置でのみ使用。'],
             references=REFERENCES)
    if inp['kind']=='spline':
        r['minimum_engagement_length_m']=inp['geometry']['engagement_length_m']*max(c['utilization'] for c in r['checks'] if c['key']!='shaft_equivalent')
        r['shaft_check_passed']=next(c['passed'] for c in r['checks'] if c['key']=='shaft_equivalent')
    if inp['kind']=='curvic' and not r['contact_model_valid']:
        r['qualifications'].append('離間を予測しています。歯部応力は完全接触を仮定した参考値で、接触状態を満たすまで設計成立として扱えません。')
    return r


def run_case(raw):
    inp=validate_input(raw); started=datetime.now(timezone.utc).isoformat(timespec='milliseconds'); result=analyze(inp)
    return {'format':'gas-turbine-shaft-joint-case','schema_version':1,'module_id':MODULE_ID,'module_version':MODULE_VERSION,
            'run_id':str(uuid.uuid4()),'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'input_hash':input_hash(inp),'input':inp,'result':result}


def read_input_file(document):
    if isinstance(document,dict) and document.get('format')=='gas-turbine-shaft-joint-case':
        if document.get('module_id')!=MODULE_ID or document.get('module_version')!=MODULE_VERSION or type(document.get('schema_version')) is not int or document['schema_version']!=1:
            raise ValueError('未対応の軸締結ケースです。')
        document=document.get('input')
    return validate_input(document)


def cycle_load(case,position):
    keys={'compressor':'compressor_power_w','turbine':'turbine_shaft_power_w','net_shaft':'net_shaft_power_w'}
    if position not in keys: raise ValueError('軸位置を選択してください。')
    if case.get('format')!='gas-turbine-cycle-case' or case.get('module_id')!='gt.cycle.simple_single_shaft': raise ValueError('サイクル計算ケースを指定してください。')
    power=case['result']['metrics'][keys[position]]; speed=case['input']['parameters']['shaft_speed_rpm']
    if type(power) not in (int,float) or not math.isfinite(power): raise ValueError('有効な軸動力がありません。')
    return dict(mode='power',power_w=power,speed_rpm=speed,
                source_note=f"保存サイクル: {case['run_id']} / {case['input']['name']} / 軸位置: {position} / module: {case['module_version']} / input_hash: {case['input_hash']}。この軸位置の動力のみ。")


if __name__=='__main__':
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser(description='Preliminary spline / curvic shaft-joint strength')
    p.add_argument('--input',type=Path); p.add_argument('--kind',choices=['spline','curvic'],default='spline'); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); inp=read_input_file(json.loads(args.input.read_text(encoding='utf-8-sig'))) if args.input else default_input(args.kind)
    args.output.write_text(json.dumps(run_case(inp),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
