"""Adiabatic, fully entrained rotating passage; zero-relative-speed plenums.

Potential and Fanno-friction operator split. All total states here are relative.
No arbitrary swirl, rotor/stator interface, windage or rotating HTC correlation.
"""
import math


def kinetic(node):
    return .5*(node.get("rpm",0)*math.pi/30*node.get("radius_m",0))**2 if node.get("frame")=="rotating" else 0.0


def initialize_temperatures(model, temperatures):
    """Seed internal rotating nodes with a common rothalpy per component."""
    from engine import cp_of
    nodes={n["id"]:n for n in model["nodes"]}
    links={key:set() for key in nodes}
    for e in model["edges"]:
        links[e["from"]].add(e["to"]);links[e["to"]].add(e["from"])
    pending=set(nodes)
    cp=cp_of(model["gas"])
    while pending:
        seen=set();queue=[next(iter(pending))]
        while queue:
            key=queue.pop()
            if key not in seen:
                seen.add(key);queue.extend(links[key]-seen)
        pending-=seen
        anchors=[n for n in model["nodes"] if n["id"] in seen and n["kind"]=="boundary" and n["frame"]=="rotating"]
        if not anchors:continue
        anchor=anchors[0]
        invariant=cp*temperatures[anchor["id"]]-kinetic(anchor)
        for key in seen:
            n=nodes[key]
            if n["frame"]=="rotating" and n["kind"]=="junction":
                temperatures[key]=min(2500,max(150,(invariant+kinetic(n))/cp))


def march(mass,pu,tu,e,gas,up,down,reverse):
    from engine import _inlet, cp_of, friction, viscosity
    cp,r,g=cp_of(gas),gas["R"],gas["gamma"]
    omega=up["rpm"]*math.pi/30
    r1,r2=up["radius_m"],down["radius_m"]
    area,d,n=e["area_m2"],e["diameter_m"],e["cells"]
    flux=mass/area
    p0,t0=pu,tu
    w,ps=_inlet(mass,p0,t0,area,gas)
    ts=t0-w*w/(2*cp)
    maxmach=w/math.sqrt(g*r*ts)
    tmin,tmax=min(ts,t0),max(ts,t0)
    re_sum,f_sum=0.,0.
    transition=False
    kval=e["k_reverse"] if reverse else e["k"]
    for i in range(n):
        ra=r1+(r2-r1)*i/n;rb=r1+(r2-r1)*(i+1)/n
        # Isentropic change of centrifugal potential, exact for the no-friction limit.
        next_t0=t0+.5*omega*omega*(rb*rb-ra*ra)/cp
        if not 150<=next_t0<=2500:return None
        p0*= (next_t0/t0)**(g/(g-1))
        t0=next_t0
        cap=area*p0/math.sqrt(r*t0)*math.sqrt(g)*(2/(g+1))**((g+1)/(2*(g-1)))
        if mass>cap:return None
        w,ps=_inlet(mass,p0,t0,area,gas)
        ts=t0-w*w/(2*cp)
        maxmach=max(maxmach,w/math.sqrt(g*r*ts))
        re=flux*d/viscosity(ts)
        f=e["friction_factor"] if e["friction"]=="fixed" else friction(re,e["roughness_m"],d)
        transition|=e["friction"]=="auto" and 2300<re<4000
        k=f*e["length_m"]/n/d+kval/n
        if k:
            aa=1-r/(2*cp)+k/4;bb=-ps/flux-w+k*w/4;cc=r*t0
            disc=bb*bb-4*aa*cc
            if disc<=0 or bb>=0:return None
            w=2*cc/(-bb+math.sqrt(disc))
            ts=t0-w*w/(2*cp)
            if ts<=0:return None
            mach=w/math.sqrt(g*r*ts)
            if mach>=1:return None
            maxmach=max(maxmach,mach)
            ps=flux*r*ts/w
            p0=ps*(t0/ts)**(g/(g-1))
        re_sum+=re;f_sum+=f
        tmin=min(tmin,ts,t0);tmax=max(tmax,ts,t0)
    return {"mass_flow_abs":mass,"outlet_total_temperature_k":t0,"heat_w":0.,
            "exit_static_pressure_pa":ps,"max_mach":maxmach,"reynolds":re_sum/n,
            "friction_factor":f_sum/n,"transition":transition,"temperature_range":[tmin,tmax]}


def rotating_branch(e,pressures,temperatures,gas,nodes):
    from engine import CalculationError, cp_of
    cp=cp_of(gas);g=gas["gamma"];r=gas["R"]
    a,b=nodes[e["from"]],nodes[e["to"]]
    pa,pb=pressures[a["id"]],pressures[b["id"]]
    ta,tb=temperatures[a["id"]],temperatures[b["id"]]
    potential=kinetic(b)-kinetic(a)
    tf=ta+potential/cp;tr=tb-potential/cp
    if min(tf,tr)<=0:raise CalculationError(f"{e['id']}: 回転ポテンシャルに対して温度が不足します。")
    forward=pa*(tf/ta)**(g/(g-1))-pb
    backward=pb*(tr/tb)**(g/(g-1))-pa
    tol=max(pa,pb)*1e-9
    if forward>tol and backward>tol:
        raise CalculationError(f"{e['id']}: 両方向の駆動条件が成立します。境界温度と回転系条件を確認してください。")
    if forward<=tol and backward<=tol:
        if min(abs(forward),abs(backward))>tol*5:
            raise CalculationError(f"{e['id']}: 流向を一意に決定できません。両端の相対全温・回転平衡条件を確認してください。")
        return {"mass_flow_abs":0.,"mass_flow_kg_s":0.,"reversed":False,"upstream":a["id"],"downstream":b["id"],
                "outlet_total_temperature_k":None,"inlet_total_temperature_k":ta,"heat_w":0.,"choked":False,
                "max_mach":0.,"reynolds":None,"friction_factor":None,"pressure_residual_pa":min(abs(forward),abs(backward)),
                "exit_static_pressure_pa":pb,"temperature_range":[ta,ta],"transition":False,
                "shaft_work_w":0.,"torque_nm":0.,"rothalpy_residual_j_kg":0.,"rotating_energy_residual_w":0.}
    reverse=backward>forward
    up,down=(b,a) if reverse else (a,b)
    pu,pd,tu=pressures[up["id"]],pressures[down["id"]],temperatures[up["id"]]
    upper=e["area_m2"]*pu/math.sqrt(r*tu)*math.sqrt(g)*(2/(g+1))**((g+1)/(2*(g-1)))
    lower=1e-16
    best=march(lower,pu,tu,e,gas,up,down,reverse)
    if best is None or best["exit_static_pressure_pa"]<pd-tol:
        raise CalculationError(f"{e['id']}: 回転流路の亜音速定常解を得られません。")
    for _ in range(38):
        trial=(lower+upper)/2
        out=march(trial,pu,tu,e,gas,up,down,reverse)
        if out is not None and out["exit_static_pressure_pa"]>=pd:
            lower,best=trial,out
        else:upper=trial
        if upper-lower<max(1e-13,upper*1e-9):break
    residual=max(0.,best["exit_static_pressure_pa"]-pd)
    choked=residual>max(.5,pu*2e-6) and best["max_mach"]>.98
    if residual>max(.5,pu*2e-6) and not choked:
        raise CalculationError(f"{e['id']}: 回転流路の圧力残差が大きすぎます。")
    delta_phi=kinetic(down)-kinetic(up)
    shaft=lower*2*delta_phi
    omega=up["rpm"]*math.pi/30
    inertia_error=cp*(best["outlet_total_temperature_k"]-tu)-delta_phi
    best.update(mass_flow_kg_s=(-1 if reverse else 1)*lower,reversed=reverse,
                upstream=up["id"],downstream=down["id"],inlet_total_temperature_k=tu,
                choked=choked,pressure_residual_pa=0. if choked else residual,
                shaft_work_w=shaft,torque_nm=shaft/omega if omega else 0.,
                rothalpy_residual_j_kg=inertia_error,rotating_energy_residual_w=lower*inertia_error)
    return best
