"""Compressor-stage bleeds, external coolant routes, and lumped inter-element mixing.

Masses are initially per kg of compressor-inlet air. No rotor pumping, metal cooling,
velocity-triangle or cooling-hole model is claimed. Mixing entropy uses absolute species
entropy including ideal mixing, not composition-dependent sensible-entropy datums.
"""
import copy
import math

if __package__:
    from .model import InputError, MODULE_ID, MODULE_VERSION
    from .thermo import air, products, Mixture, DATA, RU, species_properties, properties_at, provenance, stoichiometric_fuel_air_ratio, hydrogen_fraction
    from .elements import state, passive, rotor, temperature, solve_train, apply_mass
    from .stages import allocate
else:
    from model import InputError, MODULE_ID, MODULE_VERSION
    from thermo import air, products, Mixture, DATA, RU, species_properties, properties_at, provenance, stoichiometric_fuel_air_ratio, hydrogen_fraction
    from elements import state, passive, rotor, temperature, solve_train, apply_mass
    from stages import allocate


def absolute_entropy(gas, tt, pt):
    mole = {k: y/DATA["species"][k]["molecular_weight_kg_kmol"] for k,y in gas.mass_fractions.items()}
    total = math.fsum(mole.values())
    return math.fsum(y*(species_properties(k,tt)[2] - RU/DATA["species"][k]["molecular_weight_kg_kmol"]*math.log(mole[k]/total*pt/100000))
                     for k,y in gas.mass_fractions.items())


def combine(gas, mass, tt, coolant_mass, coolant_tt):
    a = air()
    species = {k: mass*gas.mass_fractions.get(k,0)+coolant_mass*a.mass_fractions.get(k,0) for k in DATA["species"]}
    mixed = Mixture(species)
    h = (mass*gas.h(tt)+coolant_mass*a.h(coolant_tt))/(mass+coolant_mass)
    return mixed, temperature(mixed,"h",h)


def mix(gas, mass, current, route, strict):
    c = route["input"]
    dm = c["fraction"]
    newgas, tt = combine(gas,mass,current["total_temperature_k"],dm,route["supply_total_temperature_k"])
    pt = current["total_pressure_pa"]*(1-c["mix_pressure_loss_fraction"])
    out = state(newgas,tt,pt,c["mixed_velocity_m_s"])
    out["mass_flow_factor"] = mass+dm
    sgen = ((mass+dm)*absolute_entropy(newgas,tt,pt)-mass*absolute_entropy(gas,current["total_temperature_k"],current["total_pressure_pa"])
            -dm*absolute_entropy(air(),route["supply_total_temperature_k"],route["supply_total_pressure_pa"]))
    required = current["static_pressure_pa"]*(1+c["required_pressure_margin"])
    if strict and route["supply_total_pressure_pa"] <= required:
        raise InputError(c["name"]+"の冷却供給全圧が不足しています。注入点の静圧と必要差圧を満たす抽気段・流路圧損にしてください。")
    if strict and sgen < -1e-6:
        raise InputError(c["name"]+"の混合でエントロピーが減少します。供給条件・混合圧損・流速を確認してください。")
    row = {"stage":c["target_stage"], "name":"冷却混合："+c["name"], "kind":"mixing", "inlet":current, "outlet":out,
           "coefficient_model":None, "specified_coefficient":None, "specific_work_j_kg":0., "work_per_inlet_air_j_kg":0.,
           "pressure_recovery_coefficient":None,"total_pressure_loss_coefficient":None,
           "total_pressure_loss_pa":current["total_pressure_pa"]-pt, "rotor_isentropic_efficiency":None,
           "entropy_generation_j_kg_k":sgen/(mass+dm), "entropy_flow_factor_j_kg_k":sgen,
           "route_index":route["index"],"coolant_fraction":dm,"coolant_name":c["name"],"coolant_total_temperature_k":route["supply_total_temperature_k"],
           "coolant_total_pressure_pa":route["supply_total_pressure_pa"], "required_supply_pressure_pa":required,
           "mixing_energy_residual_per_inlet_air_j_kg":(mass+dm)*newgas.h(tt)-mass*gas.h(current["total_temperature_k"])-dm*air().h(route["supply_total_temperature_k"]),
           "outlet_mass_fractions":dict(newgas.mass_fractions)}
    return newgas,mass+dm,out,row


def cooled_turbine(gas, hot_mass, tin, pin, pout, staging, config, routes, rpm):
    current=state(gas,tin,pin,config["inlet_velocity_m_s"])
    current["mass_flow_factor"]=hot_mass
    states=[current]; stages=[]; elements=[]; mass=hot_mass
    first_rotor=None; whole_log=math.log(pout/pin); cumulative=0.
    weights=staging["weights"]
    for i,settings in enumerate(config["stages"]):
        entry=current; entrygas=gas; entrymass=mass
        cumulative+=weights[i]/math.fsum(weights)
        target=pout if i==len(weights)-1 else pin*math.exp(whole_log*cumulative)
        def evaluate(log_ratio,strict=False):
            g,m,s=entrygas,entrymass,entry
            rows=[]; rotor_in=None
            def inject(point):
                nonlocal g,m,s
                for route in routes:
                    if route["input"]["target_stage"]==i+1 and route["input"]["injection"]==point:
                        g,m,s,row=mix(g,m,s,route,strict)
                        rows.append(row)
            def append(row):
                nonlocal s
                row["stage"]=i+1
                row["inlet"]["mass_flow_factor"]=m
                row["outlet"]["mass_flow_factor"]=m
                row["work_per_inlet_air_j_kg"]=m*row["specific_work_j_kg"]
                row["entropy_flow_factor_j_kg_k"]=m*row["entropy_generation_j_kg_k"]
                rows.append(row); s=row["outlet"]
            inject("before_nozzle")
            if settings["nozzle"]["enabled"]:
                append(passive(g,s,settings["nozzle"],"ノズル",strict))
            inject("before_rotor")
            rotor_in=dict(s)
            append(rotor(g,s,math.exp(log_ratio),settings["rotor_efficiency"],settings["rotor_exit_velocity_m_s"],False))
            inject("after_rotor")
            if settings["diffuser"]["enabled"]:
                append(passive(g,s,settings["diffuser"],"出口ディフューザー",strict))
            inject("after_diffuser")
            if strict and any(max(row["inlet"]["mach"],row["outlet"]["mach"])>=.95 for row in rows):
                raise InputError(f"タービン{i+1}段の冷却・要素流れがM<0.95の適用範囲を外れます。")
            return g,m,s,rows,rotor_in
        def residual(x):
            return -math.log(evaluate(x)[2]["total_pressure_pa"]/target)
        lo=0.; hi=max(.05,abs(math.log(target/entry["total_pressure_pa"]))+.1)
        if residual(lo)>1e-10:
            raise InputError(f"タービン{i+1}段の混合・静止要素圧損が段圧力予算を超えます。")
        for _ in range(12):
            if residual(hi)>=0: break
            if hi>=math.log(100): raise InputError("冷却タービンの必要ロータ膨張比が100を超えます。")
            hi=min(hi*1.5,math.log(100))
        else: raise InputError("冷却タービンの圧力配分が収束しません。")
        for _ in range(40):
            mid=(lo+hi)/2
            if residual(mid)<0: lo=mid
            else: hi=mid
        gas,mass,current,rows,rotor_in=evaluate((lo+hi)/2,True)
        if first_rotor is None: first_rotor=rotor_in
        work=math.fsum(row["work_per_inlet_air_j_kg"] for row in rows)
        has_mix=any(row["kind"]=="mixing" for row in rows)
        tis=temperature(entrygas,"entropy_thermal",entrygas.entropy_thermal(entry["total_temperature_k"])+entrygas.r*math.log(current["total_pressure_pa"]/entry["total_pressure_pa"]))
        eta=None if has_mix else work/(entrymass*(entrygas.h(entry["total_temperature_k"])-entrygas.h(tis)))
        diameter=staging["mean_diameters_m"][i] if staging["mean_diameters_m"] is not None else None
        speed=math.pi*diameter*rpm/60 if diameter is not None else None
        stages.append({"stage":i+1,"inlet":entry,"outlet":current,"pressure_ratio":entry["total_pressure_pa"]/current["total_pressure_pa"],
                       "temperature_change_k":current["total_temperature_k"]-entry["total_temperature_k"],
                       "specific_work_j_kg":work/entrymass,"work_per_inlet_air_j_kg":work,"isentropic_efficiency":eta,
                       "isentropic_exit_temperature_k":tis if not has_mix else None,"pressure_log_fraction":math.log(current["total_pressure_pa"]/entry["total_pressure_pa"])/whole_log,
                       "entropy_generation_j_kg_k":math.fsum(row["entropy_flow_factor_j_kg_k"] for row in rows)/mass,
                       "entropy_flow_factor_j_kg_k":math.fsum(row["entropy_flow_factor_j_kg_k"] for row in rows),
                       "mean_diameter_m":diameter,"blade_speed_m_s":speed,"loading_coefficient":work/entrymass/speed**2 if speed else None,
                       "outlet_pressure_residual_pa":current["total_pressure_pa"]-target,"cooled_stage":has_mix})
        states.append(current); elements.extend(rows)
    work=math.fsum(row["work_per_inlet_air_j_kg"] for row in stages)
    for row in stages: row["work_fraction"]=row["work_per_inlet_air_j_kg"]/work
    return {"count":len(stages),"distribution":"pressure","method":"cooled_element_network","polytropic_efficiency":None,
            "specified_overall_isentropic_efficiency":None,"recovered_overall_isentropic_efficiency":None,
            "specific_work_j_kg":work/hot_mass,"work_per_inlet_air_j_kg":work,"overall_pressure_ratio":pin/pout,
            "outlet_pressure_residual_pa":current["total_pressure_pa"]-pout,"stages":stages,"states":states,"elements":elements,
            "first_rotor_inlet":first_rotor,"outlet_mass_fractions":dict(gas.mass_fractions),"outlet_gas":gas}


def mass_scaled(train, ma, mechanical=1.):
    result=copy.deepcopy({k:v for k,v in train.items() if k!="outlet_gas"})
    def scale_state(s):
        s["mass_flow_kg_s"]=ma*s["mass_flow_factor"]
        if "density_kg_m3" in s: s["effective_flow_area_m2"]=s["mass_flow_kg_s"]/(s["density_kg_m3"]*s["velocity_m_s"])
    for s in result["states"]: scale_state(s)
    if "first_rotor_inlet" in result: scale_state(result["first_rotor_inlet"])
    for row in result["stages"]+result.get("elements",[]):
        scale_state(row["inlet"]); scale_state(row["outlet"])
        row["power_w"]=ma*row["work_per_inlet_air_j_kg"]
        row["transmitted_power_w"]=row["power_w"]*mechanical
        row["entropy_generation_w_k"]=ma*row["entropy_flow_factor_j_kg_k"]
        if row.get("kind")=="mixing": row["coolant_mass_flow_kg_s"]=ma*row["coolant_fraction"]
    result["total_power_w"]=ma*train["work_per_inlet_air_j_kg"]
    result["sum_stage_power_w"]=math.fsum(s["power_w"] for s in result["stages"])
    return result


def analyze_cooled(inp):
    v=inp["parameters"]; cfg=inp["cooling"]; a=air()
    flows=[c for c in cfg["circuits"] if c["fraction"]>0]
    bleed=math.fsum(c["fraction"] for c in flows); burner_air=1-bleed
    t1=v["ambient_temperature_k"]; pa=v["ambient_pressure_pa"]
    p1=pa*(1-v["inlet_pressure_loss"]); p2=p1*v["compressor_pressure_ratio"]
    p3=p2*(1-v["combustor_pressure_loss"]); p4=pa/(1-v["exhaust_pressure_loss"])
    if p3<=p4: raise InputError("圧損を考慮するとタービンが膨張できません。")
    t2s=temperature(a,"entropy_thermal",a.entropy_thermal(t1)+a.r*math.log(p2/p1))
    if inp["elements"]["compressor"]["enabled"]:
        comp=solve_train(a,t1,p1,p2,inp["staging"]["compressor"],inp["elements"]["compressor"],"compressor",v["shaft_speed_rpm"])
    else:
        t2=temperature(a,"h",a.h(t1)+(a.h(t2s)-a.h(t1))/v["compressor_efficiency"])
        comp=allocate(a,t1,p1,t2,p2,1.,v["compressor_efficiency"],inp["staging"]["compressor"],"compressor",v["shaft_speed_rpm"])
    remaining=1.
    comp["states"][0]["mass_flow_factor"]=1.
    for i,row in enumerate(comp["stages"]):
        row["inlet"]=dict(row["inlet"]); row["outlet"]=dict(row["outlet"])
        row["inlet"]["mass_flow_factor"]=remaining; row["outlet"]["mass_flow_factor"]=remaining
        row["work_per_inlet_air_j_kg"]=remaining*row["specific_work_j_kg"]
        row["entropy_flow_factor_j_kg_k"]=remaining*row["entropy_generation_j_kg_k"]
        for e in comp.get("elements",[]):
            if e["stage"]==i+1:
                e["inlet"]=dict(e["inlet"]); e["outlet"]=dict(e["outlet"])
                e["inlet"]["mass_flow_factor"]=remaining; e["outlet"]["mass_flow_factor"]=remaining
                e["work_per_inlet_air_j_kg"]=remaining*e["specific_work_j_kg"]
                e["entropy_flow_factor_j_kg_k"]=remaining*e["entropy_generation_j_kg_k"]
        row["bleed_fraction"]=math.fsum(c["fraction"] for c in flows if c["source_stage"]==i+1)
        remaining-=row["bleed_fraction"]
        row["downstream_mass_flow_factor"]=remaining
        comp["states"][i+1]=dict(row["outlet"],mass_flow_factor=remaining)
    comp["work_per_inlet_air_j_kg"]=math.fsum(s["work_per_inlet_air_j_kg"] for s in comp["stages"])
    for row in comp["stages"]: row["work_fraction"]=row["work_per_inlet_air_j_kg"]/comp["work_per_inlet_air_j_kg"]
    t2=comp["states"][-1]["total_temperature_k"]
    routes=[]
    for c in flows:
        source=comp["stages"][c["source_stage"]-1]["outlet"]
        tt=temperature(a,"h",a.h(source["total_temperature_k"])+c["heat_j_kg"])
        routes.append({"index":len(routes),"input":c,"source_total_temperature_k":source["total_temperature_k"],"source_total_pressure_pa":source["total_pressure_pa"],
                       "supply_total_temperature_k":tt,"supply_total_pressure_pa":source["total_pressure_pa"]*(1-c["pressure_loss_fraction"])})
    def burn(tt):
        if tt<=t2: raise InputError("燃焼器出口温度は圧縮機出口温度より高くしてください。")
        lo,hi=0.,stoichiometric_fuel_air_ratio(inp["thermodynamics"])
        def residual(f): return (1+f)*products(f,inp["thermodynamics"]).h(tt)-a.h(t2)-f*v["combustion_efficiency"]*v["fuel_lhv_j_kg"]
        if residual(hi)>=0: raise InputError("冷却条件の燃焼温度に必要な燃料が量論比を超えます。")
        for _ in range(55):
            mid=(lo+hi)/2
            if residual(mid)>0: lo=mid
            else: hi=mid
        f=(lo+hi)/2
        return products(f,inp["thermodynamics"]),burner_air*f
    target=v["turbine_inlet_temperature_k"]
    if cfg["temperature_basis"]=="combustor_exit":
        t3=target
    else:
        lo=max(500.,t2+.001); hi=cfg["combustor_exit_limit_k"]
        def first(tt):
            g,f=burn(tt); mass=burner_air+f
            for route in routes:
                c=route["input"]
                if c["target_stage"]==1 and c["injection"] in ("before_nozzle","before_rotor"):
                    g,tt=combine(g,mass,tt,c["fraction"],route["supply_total_temperature_k"]); mass+=c["fraction"]
            return tt
        if hi<=lo or first(lo)>target or first(hi)<target: raise InputError("1段目ロータ入口温度を燃焼器出口温度上限内で満たせません。")
        for _ in range(34):
            mid=(lo+hi)/2
            if first(mid)<target: lo=mid
            else: hi=mid
        t3=(lo+hi)/2
    if t3>cfg["combustor_exit_limit_k"]: raise InputError("燃焼器出口温度が設定上限を超えています。")
    gas,fuel=burn(t3); hot_mass=burner_air+fuel
    turbine=cooled_turbine(gas,hot_mass,t3,p3,p4,inp["staging"]["turbine"],inp["elements"]["turbine"],routes,v["shaft_speed_rpm"])
    wc=comp["work_per_inlet_air_j_kg"]; wt=turbine["work_per_inlet_air_j_kg"]
    shaft_specific=v["mechanical_efficiency"]*wt-wc
    ma=v["air_mass_flow_kg_s"]
    if inp["mode"]=="target_power":
        if shaft_specific<=0: raise InputError("冷却条件では正の軸出力が得られません。")
        ma=(v["target_net_power_w"]+v["auxiliary_power_w"])/(v["generator_efficiency"]*shaft_specific)
        if not .001<=ma<=1000: raise InputError("必要空気流量が0.001〜1000 kg/sの範囲外です。")
    mf=ma*fuel; mg=ma*(1+fuel); wc*=ma; wt*=ma
    shaft=v["mechanical_efficiency"]*wt-wc; generating=shaft>0
    gross=shaft*v["generator_efficiency"] if generating else None
    net=gross-v["auxiliary_power_w"] if generating else None
    fuel_heat=mf*v["fuel_lhv_j_kg"]
    heat=ma*math.fsum(c["fraction"]*c["heat_j_kg"] for c in flows)
    comb_loss=(1-v["combustion_efficiency"])*fuel_heat
    mech_loss=(1-v["mechanical_efficiency"])*wt
    gen_loss=shaft*(1-v["generator_efficiency"]) if generating else None
    outgas=turbine["outlet_gas"]; t4=turbine["states"][-1]["total_temperature_k"]
    exhaust=mg*outgas.h(t4)-ma*a.h(t1)
    balance=fuel_heat+heat-(shaft+mech_loss+comb_loss+exhaust)
    electric_balance=fuel_heat+heat-(net+v["auxiliary_power_w"]+gen_loss+mech_loss+comb_loss+exhaust) if generating else None
    def ideal(g,tt,pt):
        tis=temperature(g,"entropy_thermal",g.entropy_thermal(tt)+g.r*math.log(p4/pt))
        return g.h(tt)-g.h(tis)
    ideal_hot=hot_mass*ideal(gas,t3,p3)
    ideal_cool=math.fsum(c["input"]["fraction"]*ideal(a,c["supply_total_temperature_k"],c["supply_total_pressure_pa"]) for c in routes)
    thermal_eta=(wt/ma)/(ideal_hot+ideal_cool) if ideal_hot+ideal_cool>0 else None
    comp_scaled=mass_scaled(comp,ma); turbine_scaled=mass_scaled(turbine,ma,v["mechanical_efficiency"])
    comp_scaled["bleed_aware"]=True; turbine_scaled["thermodynamic_efficiency"]=thermal_eta
    stations=[]
    for index,name,tt,pt,mass,g in (("0","大気",t1,pa,ma,a),("1","圧縮機入口",t1,p1,ma,a),
                                   ("2","燃焼器へ（抽気後）",t2,p2,ma*burner_air,a),("3","燃焼器出口（冷却混合前）",t3,p3,ma*hot_mass,gas),
                                   ("4","タービン出口（冷却混合後）",t4,p4,mg,outgas),("5","排気境界",t4,pa,mg,outgas)):
        stations.append({"id":index,"name":name,"total_temperature_k":tt,"total_pressure_pa":pt,"mass_flow_kg_s":mass,
                         "fluid":"air" if index in ("0","1","2") else "gas",**properties_at(g,tt)})
    for route in routes:
        route["mass_flow_kg_s"]=ma*route["input"]["fraction"]
        route["external_heat_w"]=route["mass_flow_kg_s"]*route["input"]["heat_j_kg"]
    positive=generating and net>0
    metrics={"air_mass_flow_kg_s":ma,"fuel_mass_flow_kg_s":mf,"gas_mass_flow_kg_s":mg,"fuel_air_ratio":fuel,"combustor_fuel_air_ratio":fuel/burner_air,
             "compressor_power_w":wc,"turbine_gas_power_w":wt,"turbine_shaft_power_w":v["mechanical_efficiency"]*wt,
             "net_shaft_power_w":shaft,"gross_electric_power_w":gross,"net_electric_power_w":net,"fuel_heat_input_w":fuel_heat,
             "shaft_efficiency_lhv":shaft/fuel_heat if generating else None,"electric_efficiency_lhv":net/fuel_heat if positive else None,
             "heat_rate_kj_kwh":fuel_heat/net*3600 if positive else None,"specific_fuel_consumption_kg_kwh":mf/net*3.6e6 if positive else None,
             "exhaust_temperature_k":t4,"turbine_expansion_ratio":p3/p4,"back_work_ratio":wc/(wt*v["mechanical_efficiency"]),
             "gamma_air":a.gamma(t1),"gamma_gas":gas.gamma(t3),"compressor_isentropic_exit_temperature_k":t2s,
             "turbine_isentropic_exit_temperature_k":None,"compressor_effective_efficiency":comp["recovered_overall_isentropic_efficiency"],
             "turbine_effective_efficiency":None,"turbine_thermodynamic_efficiency":thermal_eta,"combustor_air_mass_flow_kg_s":ma*burner_air,
             "cooling_air_mass_flow_kg_s":ma*bleed,"combustor_exit_temperature_k":t3,"first_rotor_inlet_temperature_k":turbine["first_rotor_inlet"]["total_temperature_k"],
             "electric_efficiency_total_heat":net/(fuel_heat+max(heat,0)) if positive else None}
    if not all(math.isfinite(x) for x in metrics.values() if x is not None): raise InputError("冷却計算が非有限値です。")
    return {"schema_version":5,"module_id":MODULE_ID,"module_version":MODULE_VERSION,"status":"success" if positive else "non_generating",
            "model":"nasa9_cooled_element_network","enthalpy_reference_temperature_k":298.15,"metrics":metrics,"stations":stations,
            "stage_results":{"compressor":comp_scaled,"turbine":turbine_scaled},
            "thermodynamics":{"model":"nasa9_frozen","fuel":inp["thermodynamics"]["fuel"],"dataset":provenance(),"air_mass_fractions":dict(a.mass_fractions),
                              "product_mass_fractions":dict(outgas.mass_fractions),"combustor_product_mass_fractions":dict(gas.mass_fractions),
                              "fuel_element_mass_fractions":{"C":1-hydrogen_fraction(inp["thermodynamics"]),"H":hydrogen_fraction(inp["thermodynamics"])},
                              "equivalence_ratio":fuel/burner_air/stoichiometric_fuel_air_ratio(inp["thermodynamics"]),
                              "stoichiometric_fuel_air_ratio":stoichiometric_fuel_air_ratio(inp["thermodynamics"])},
            "cooling":{"enabled":True,"fraction_basis":"compressor_inlet_air","total_fraction":bleed,"temperature_basis":cfg["temperature_basis"],
                       "routes":routes,"external_heat_w":heat,"hot_turbine_mass_flow_kg_s":ma*hot_mass,"outlet_mass_flow_kg_s":mg,
                       "turbine_ideal_hot_power_w":ma*ideal_hot,"turbine_ideal_coolant_power_w":ma*ideal_cool,
                       "efficiency_definition":"Thermodynamic: gas work divided by sum of separate isentropic inlet-to-exit work potentials. No coolant pumping work model."},
            "energy_balance":{"combustion_loss_w":comb_loss,"mechanical_loss_w":mech_loss,"generator_loss_w":gen_loss,
                              "auxiliary_power_w":v["auxiliary_power_w"],"exhaust_net_sensible_energy_w":exhaust,"cooling_external_heat_w":heat,
                              "shaft_balance_residual_w":balance,"electric_balance_residual_w":electric_balance,
                              "mass_balance_residual_kg_s":mg-ma-mf},
            "warnings":["冷却は抽気・流路・要素間完全混合の等価モデルです。ロータ内部の遠心ポンピング・翼伝熱・金属温度・冷却孔・速度三角形は計算しません。",
                        "温度依存NASA物性・凍結組成です。燃焼の化学平衡・解離は未対応です。冷却タービンの単一入口ηttは表示せず、複数入口の熱力学効率を別表示します。",
                        "冷却流路の熱授受は外部との熱交換です。翼から冷却空気への内部熱移動として入力すると熱を二重計上するため、その用途には使わないでください。"]}
