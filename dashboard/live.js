// Claude Design v3 presentation adapter. All operational data comes from backend.
const h = React.createElement;
const DARK = {bg:'#0B0D10',p1:'#111418',p2:'#181C22',ln:'#232830',ln2:'#313843',tx:'#E6E8EB',t2:'#A0A8B3',t3:'#6E7682',g:'#3FB97A',y:'#E0B040',r:'#E5574F',b:'#5AA2F0',gs:'rgba(63,185,122,.13)',ys:'rgba(224,176,64,.14)',rs:'rgba(229,87,79,.15)',bs:'rgba(90,162,240,.13)',ink:'#0B0D10'};
const LIGHT = {...DARK,bg:'#F2F3F5',p1:'#FFFFFF',p2:'#F4F5F7',ln:'#E1E4E8',ln2:'#C9CED5',tx:'#14171C',t2:'#48505A',t3:'#737B86',ink:'#FFFFFF'};
const fmt = (x,n=1) => x == null ? '—' : Number(x).toLocaleString('ru-RU',{maximumFractionDigits:n});
const hm = t => t ? t.slice(11,19) : '—';
// UTC parsing is only a numeric axis encoding of naive dataset strings, not a timezone claim.
const sec = t => t ? Date.parse(t+'Z')/1000 : 0;
const RISK = {ok:['В графике','●','g'],risk:['Риск','◆','y'],high:['Высокий','▲','r']};

// OpenLayers instance survives stream updates: live positions never reset the user's camera.
class DispatchMap extends React.Component {
  state={tileError:false,showPlans:true};
  componentDidMount() {
    const ol=window.ol;
    this.tiles=new ol.source.OSM({maxZoom:19});
    this.lines=new ol.source.Vector();this.trails=new ol.source.Vector();
    this.map=new ol.Map({target:this.node,controls:[new ol.control.Attribution({collapsible:false}),new ol.control.ScaleLine({units:'metric'})],
      layers:[new ol.layer.Tile({source:this.tiles,className:'street-tiles'}),new ol.layer.Vector({source:this.lines}),new ol.layer.Vector({source:this.trails})],
      view:new ol.View({center:ol.proj.fromLonLat([37.60,55.72]),zoom:11,minZoom:8,maxZoom:19})});
    this.tiles.on('tileloaderror',()=>this.setState({tileError:true}));
    this.tiles.on('tileloadend',()=>{if(this.state.tileError)this.setState({tileError:false})});
    this.markerById=new Map();
    this.tooltip=document.createElement('div');this.tooltip.className='map-tooltip';this.tooltip.hidden=true;
    this.tooltipOverlay=new ol.Overlay({element:this.tooltip,offset:[12,12],stopEvent:false});this.map.addOverlay(this.tooltipOverlay);
    this.map.on('pointermove',event=>{
      const feature=event.dragging?null:this.map.forEachFeatureAtPixel(event.pixel,f=>f,{hitTolerance:5});
      this.tooltip.hidden=!feature;this.node.style.cursor=feature?'pointer':'';
      if(feature){this.tooltip.textContent=feature.get('label');this.tooltipOverlay.setPosition(event.coordinate);}
    });
    this.map.on('singleclick',event=>{
      const feature=this.map.forEachFeatureAtPixel(event.pixel,f=>f,{hitTolerance:5});
      if(feature?.get('routeId'))this.props.onRoute(feature.get('routeId'));
    });
    this.observer=new ResizeObserver(()=>this.map?.updateSize());this.observer.observe(this.node);
    this.props.onReady(this.map);this.updateLayers(true);
    this.map.getView().on('change:resolution',()=>this.updateLabels());
    this.resizeTimer=setTimeout(()=>{this.map.updateSize();this.fit()},150);
  }
  componentWillUnmount(){clearTimeout(this.resizeTimer);this.observer?.disconnect();this.map?.dispose();this.map=null;}
  project(lat,lon){return window.ol.proj.fromLonLat([lon,lat]);}
  addLine(source,points,{color,width,opacity=1,dash,label,routeId}){
    const ol=window.ol,rgba=ol.color.asArray(color).slice();rgba[3]=opacity;
    const feature=new ol.Feature({geometry:new ol.geom.LineString(points),label,routeId});
    feature.setStyle(new ol.style.Style({stroke:new ol.style.Stroke({color:rgba,width,lineDash:dash})}));source.addFeature(feature);
  }
  componentDidUpdate(prev,oldState){
    if(!this.map)return;
    this.updateLayers(prev.plans!==this.props.plans||prev.route!==this.props.route||oldState.showPlans!==this.state.showPlans);
    if(prev.route!==this.props.route)this.fit();
    if(prev.focus!==this.props.focus&&this.props.focus)this.fit(this.props.focus.route);
  }
  positions(route=this.props.route){
    const plans=Object.values(this.props.plans).filter(p=>!route||p.route_id===route);
    return plans.flatMap(p=>p.stops).filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon)).map(p=>[p.lat,p.lon]);
  }
  fit(route=this.props.route){
    const points=route?this.positions(route):this.props.vehicles.filter(v=>v.lat!=null&&v.lon!=null).map(v=>[v.lat,v.lon]);
    const pts=points.length?points:this.positions(route);
    if(pts.length)this.map.getView().fit(window.ol.extent.boundingExtent(pts.map(p=>this.project(p[0],p[1]))),{padding:[80,65,75,65],maxZoom:14,duration:0});
  }
  updateLabels(){
    const detailed=this.map.getView().getZoom()>=12||!!this.props.route;
    this.markerById.forEach(({element,v})=>element.classList.toggle('show-label',detailed||v.next_stop_risk_level!=='ok'));
  }
  updateLayers(redraw){
    const ol=window.ol,vs=this.props.vehicles,colors={ok:'#38b886',risk:'#ffc34a',high:'#ff625c'};
    const signature=vs.map(v=>`${v.vehicle_id}:${v.risk_level}:${v.segment_id}`).join('|');
    if(redraw||signature!==this.signature){
      this.signature=signature;this.lines.clear();
      if(this.state.showPlans)Object.values(this.props.plans).filter(p=>!this.props.route||p.route_id===this.props.route).forEach(plan=>{
        const v=vs.find(v=>v.route_id===plan.route_id),active=!!this.props.route;
        const edges=new Set();
        const activeIndex=plan.stops.findIndex((p,i)=>i>0&&v?.segment_id===`${plan.stops[i-1].event_id}:${p.event_id}`);
        const activeEdge=activeIndex>0?[plan.stops[activeIndex-1],plan.stops[activeIndex]].map(p=>`${p.lat},${p.lon}`).sort().join(':'):null;
        for(let i=1;i<plan.stops.length;i++){
          const a=plan.stops[i-1],b=plan.stops[i];
          if(![a.lat,a.lon,b.lat,b.lon].every(Number.isFinite))continue;
          const ka=`${a.lat},${a.lon}`,kb=`${b.lat},${b.lon}`,key=[ka,kb].sort().join(':');
          if(ka===kb||edges.has(key))continue;edges.add(key);
          // Full-day control-point connections; no invented road snapping.
          const current=key===activeEdge;
          const color=current?colors[v.risk_level]:active?'#65b7ff':'#77a9d1';
          this.addLine(this.lines,[this.project(a.lat,a.lon),this.project(b.lat,b.lon)],{color,width:current?7:active?3:2,
            opacity:current?.95:active?.65:.36,dash:current?undefined:[5,7],routeId:plan.route_id,
            label:`План ТС ${plan.vehicle_ids?.[0]||plan.route_id.replace('plan-','')} · ${a.name} → ${b.name}`});
        }
        if(active){
          const unique=new Set();plan.stops.forEach(p=>{const key=`${p.lat}:${p.lon}`;if(unique.has(key)||!Number.isFinite(p.lat)||!Number.isFinite(p.lon))return;unique.add(key);
            const feature=new ol.Feature({geometry:new ol.geom.Point(this.project(p.lat,p.lon)),label:p.name,routeId:plan.route_id});
            feature.setStyle(new ol.style.Style({image:new ol.style.Circle({radius:3,stroke:new ol.style.Stroke({color:'#b8dfff',width:1}),fill:new ol.style.Fill({color:'#24394a'})})}));
            this.lines.addFeature(feature);
          });
        }
      });
    }
    if(this.lastTracks!==this.props.tracks||this.lastTrackSignature!==signature){
      this.lastTracks=this.props.tracks;this.lastTrackSignature=signature;this.trails.clear();
      (this.props.tracks||[]).forEach(track=>{
        const v=vs.find(v=>v.vehicle_id===track.vehicle_id);if(!v)return;
        let part=[],previous=null;const parts=[];
        track.points.forEach(p=>{
          const position=this.project(p.lat,p.lon),dt=previous?sec(p.event_time)-sec(previous.event_time):0;
          // Break display lines across outages / implausible jumps, without altering Cleaner.
          if(previous&&(dt>120||dt<=0||ol.sphere.getDistance([p.lon,p.lat],[previous.lon,previous.lat])/dt>115/3.6)){if(part.length>1)parts.push(part);part=[];}
          part.push(position);previous=p;
        });if(part.length>1)parts.push(part);
        const color=v.is_stale?'#8c9baa':v.next_stop_predicted_delay_min==null?'#71b4dd':colors[v.next_stop_risk_level];
        parts.forEach(points=>this.addLine(this.trails,points,{color,width:this.props.route?5:3.5,opacity:.9,
          label:`GPS-трек ТС ${track.vehicle_id} · последние 30 мин`,routeId:track.route_id}));
      });
    }
    const ids=new Set();
    vs.filter(v=>Number.isFinite(v.lat)&&Number.isFinite(v.lon)).forEach(v=>{
      ids.add(v.vehicle_id);const color=v.is_stale?'#8c9baa':v.next_stop_predicted_delay_min==null?'#71b4dd':colors[v.next_stop_risk_level];
      let entry=this.markerById.get(v.vehicle_id);
      if(!entry){
        const element=document.createElement('button');element.type='button';element.className='vehicle-marker';
        const pin=document.createElement('span');pin.className='vehicle-pin';
        const arrow=document.createElement('i'),glyph=document.createElement('b'),label=document.createElement('span');label.className='vehicle-label';
        pin.append(arrow,glyph);element.append(pin,label);
        const marker=new ol.Overlay({element,positioning:'center-center',stopEvent:true});this.map.addOverlay(marker);
        entry={marker,element,pin,glyph,label,v};this.markerById.set(v.vehicle_id,entry);
        element.addEventListener('click',()=>this.props.onRoute(entry.v.route_id));
      }
      entry.v=v;
      entry.pin.style.setProperty('--vehicle-color',color);
      entry.pin.style.setProperty('--heading',`${Number.isFinite(v.heading)?v.heading:0}deg`);
      entry.element.style.opacity=v.is_stale?'.6':'1';
      entry.glyph.textContent=['high','risk'].includes(v.next_stop_risk_level)?'!':'';
      entry.marker.setPosition(this.project(v.lat,v.lon));
      entry.element.parentElement.style.zIndex=v.next_stop_risk_level==='high'?'1000':'100';
      const label=`${v.vehicle_id}${v.next_stop_eta?' · ETA '+hm(v.next_stop_eta):''}${v.next_stop_predicted_delay_min!=null?' · '+(v.next_stop_predicted_delay_min>0?'+':'')+fmt(v.next_stop_predicted_delay_min)+' мин':''}${v.is_stale?' · нет связи':''}`;
      entry.element.setAttribute('aria-label',`ТС ${label}, следующая остановка ${v.next_stop||'не определена'}`);
      entry.label.textContent=label;
    });
    this.markerById.forEach((entry,id)=>{if(!ids.has(id)){this.map.removeOverlay(entry.marker);this.markerById.delete(id)}});
    if(!this.fitted&&vs.some(v=>v.lat!=null)){this.fit();this.fitted=true;}
    this.updateLabels();
  }
  render(){return h('div',{className:`dispatch-map ${this.props.theme==='dark'?'map-dark':''}`,style:{height:this.props.route?340:'100%'}},
    h('div',{ref:n=>this.node=n,className:'map-host','aria-label':'Карта транспорта на улицах Москвы'}),
    h('div',{className:'map-toolbar'},h('span',{className:'map-heading'},this.props.route?'ПЛАН ДВИЖЕНИЯ':'МОСКВА · ТРАНСПОРТ'),
      h('button',{onClick:()=>this.fit(),title:'Показать все ТС / весь выбранный план'},'⊕ Весь охват'),
      h('button',{'aria-pressed':this.state.showPlans,onClick:()=>this.setState({showPlans:!this.state.showPlans})},this.state.showPlans?'✓ Планы':'Планы'),
      h('button',{onClick:()=>this.map.getView().setZoom(this.map.getView().getZoom()+1),'aria-label':'Приблизить карту'},'+'),h('button',{onClick:()=>this.map.getView().setZoom(this.map.getView().getZoom()-1),'aria-label':'Отдалить карту'},'−')),
    h('div',{className:'map-caption'},this.state.tileError?'Подложка недоступна · проверьте интернет':this.props.stale?'Нет связи · последние известные координаты':'Сплошная — GPS-трек · пунктир — план по контрольным точкам'),
    h('div',{className:'map-key'},h('span',null,'🔵 Нет ETA'),h('span',{style:{color:'#38b886'}},'● ≤2 мин'),h('span',{style:{color:'#ffc34a'}},'● 2–5 мин'),h('span',{style:{color:'#ff625c'}},'● >5 мин'))
  );}
}

class Component extends DCLogic {
  state = {vehicles:{},alerts:{},summary:{},status:{},routes:[],plans:{},tracks:[],route:null,timeline:null,
    problems:[],screen:'overview',theme:'dark',q:'',risk:'all',drawer:null,menu:false,
    connected:false,error:null,wi:'reserve',scenario:null,zoom:1,mapFocus:null};

  async api(path,body) {
    const response = await fetch(path,body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    if(!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }
  componentDidMount() {
    this.alive=true; this.load(); this.connect();
    this.poll=setInterval(()=>this.refresh(),3000);
  }
  componentWillUnmount() {this.alive=false;clearInterval(this.poll);clearTimeout(this.retry);this.socket?.close();}
  async load() {
    try {
      const routes=await this.api('/api/routes');
      const details=await Promise.all(routes.map(r=>this.api('/api/routes/'+encodeURIComponent(r.route_id))));
      if(!this.alive)return;
      this.setState({routes,plans:Object.fromEntries(details.map(r=>[r.route_id,r])),route:routes[0]?.route_id});
      this.refresh();
    } catch(e) {if(this.alive)this.setState({error:e.message});}
  }
  connect() {
    if(!this.alive)return;
    const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/stream`);
    this.socket=ws;
    ws.onopen=()=>this.setState({connected:true,error:null});
    ws.onmessage=e=>{
      this.receivedAt=Date.now();
      const d=JSON.parse(e.data);
      this.setState({connected:true});
      if(d.type==='snapshot')this.setState({vehicles:d.vehicles,alerts:d.alerts,summary:d.summary,status:d.system_status});
      else if(d.type==='update')this.setState(s=>{
        const vehicles={...s.vehicles},alerts={...s.alerts};
        d.vehicles.upsert.forEach(v=>vehicles[v.vehicle_id]=v);
        d.vehicles.removed.forEach(id=>delete vehicles[id]);
        d.alerts.upsert.forEach(a=>alerts[a.alert_id]=a);
        return {vehicles,alerts,summary:d.summary,status:d.system_status};
      });
    };
    ws.onclose=()=>{if(!this.alive)return;this.setState({connected:false});this.retry=setTimeout(()=>this.connect(),1500)};
    ws.onerror=()=>ws.close();
  }
  async refresh() {
    if(this.receivedAt&&Date.now()-this.receivedAt>5000)this.setState({connected:false});
    if(this.refreshing)return;this.refreshing=true;
    try {
      const [p,tracks]=await Promise.all([this.api('/api/segments/problems'),this.api('/api/map/tracks')]);
      const id=this.state.route;
      const timeline=id ? await this.api(`/api/routes/${encodeURIComponent(id)}/timeline?direction=plan`) : null;
      if(this.alive)this.setState(s=>({problems:p.items,tracks,...(s.route===id?{timeline}:{})}));
    } catch(e) {if(this.alive)this.setState({error:e.message});}
    finally {this.refreshing=false;}
  }
  selectRoute(id) {this.setState({route:id,screen:'route',drawer:null,scenario:null,timeline:null},()=>this.refresh());}
  async control() {
    try {await this.api('/api/demo/control',{action:this.state.status.stream_connected?'disconnect':'reconnect'});}
    catch(e){this.setState({error:e.message});}
  }
  async scenario() {
    try {
      const result=await this.api('/api/whatif',{route_id:this.state.route,action:this.state.wi,minutes:2});
      this.setState({scenario:result,error:null});
    } catch(e){this.setState({error:e.message});}
  }
  map(C,onlyRoute=false) {
    const S=this.state,route=onlyRoute?S.route:null;
    const vehicles=Object.values(S.vehicles).filter(v=>(!route||v.route_id===route)&&
      (!S.q||`${v.vehicle_id} ${v.route_id}`.includes(S.q))&&(S.risk==='all'||v.next_stop_risk_level===S.risk));
    return h(DispatchMap,{vehicles,plans:S.plans,tracks:S.tracks,route,theme:S.theme,focus:S.mapFocus,stale:!S.connected||S.status.status==='degraded',
      onReady:map=>{this.activeMap=map;},onRoute:id=>this.selectRoute(id)});
  }
  chart(C) {
    const t=this.state.timeline;if(!t)return h('p',{style:{padding:20}},'Ожидание графика…');
    const now=sec(t.virtual_time),lo=now-1800,hi=now+1800;
    const visible=t.planned.filter(p=>sec(p.time)>=lo&&sec(p.time)<=hi);
    if(!visible.length)return h('p',{style:{padding:20}},'В текущем часовом окне нет плановых событий.');
    const orders=visible.map(p=>p.stop_order),bottom=Math.min(...orders),top=Math.max(...orders);
    const X=p=>80+(sec(p.time)-lo)/(hi-lo)*800,Y=p=>260-(p.stop_order-bottom)/Math.max(1,top-bottom)*220;
    const E=[];
    for(let m=-30;m<=30;m+=10){const xx=80+(m+30)/60*800;E.push(h('line',{key:'g'+m,x1:xx,x2:xx,y1:20,y2:265,stroke:C.ln}),h('text',{key:'l'+m,x:xx,y:290,fill:C.t2,fontSize:12,textAnchor:'middle'},m===0?'сейчас':`${m>0?'+':''}${m} мин`));}
    E.push(h('polyline',{key:'plan',points:visible.map(p=>`${X(p)},${Y(p)}`).join(' '),fill:'none',stroke:C.t3,strokeDasharray:'5 5'}));
    [['actual',C.tx],['forecast',C.b]].forEach(([name,color])=>t[name].filter(p=>sec(p.time)>=lo&&sec(p.time)<=hi&&p.stop_order>=bottom&&p.stop_order<=top).forEach(p=>E.push(h('circle',{key:name+p.event_id,cx:X(p),cy:Y(p),r:5,fill:color},h('title',null,`${name} · ${hm(p.time)} · событие ${p.event_id}`)))));
    E.push(h('text',{key:'label',x:8,y:15,fill:C.t2,fontSize:11},'Порядок события в плане'));
    return h('svg',{viewBox:'0 0 920 310',width:'100%',role:'img','aria-label':'План, опубликованные факты и прогнозы прибытия'},E);
  }
  renderVals() {
    const S=this.state,C=S.theme==='light'?LIGHT:DARK,V=k=>C[k],sum=S.summary;
    const rootVars={height:'100vh',width:'100%',position:'relative'};Object.keys(C).forEach(k=>rootVars['--'+k]=C[k]);
    const stale=!S.connected||S.status.status==='degraded',now=sec(S.status.virtual_time);
    const etaAll=Object.values(S.vehicles).filter(v=>v.next_stop_predicted_delay_min>2&&v.next_stop_eta&&v.next_stop);
    const matching=etaAll.filter(v=>(S.risk==='all'||v.next_stop_risk_level===S.risk)&&
      (!S.q||`${v.vehicle_id} ${v.route_id} ${v.next_stop}`.toLowerCase().includes(S.q.toLowerCase())))
      .sort((a,b)=>(a.next_stop_risk_level===b.next_stop_risk_level?0:a.next_stop_risk_level==='high'?-1:1)||sec(a.next_stop_eta)-sec(b.next_stop_eta));
    const filtered=matching;
    const countdown=t=>{const m=(sec(t)-now)/60;return m>0?`через ${Math.ceil(m)} мин`:'плановое время прошло'};
    const methodLabel=m=>m==='optuna-features-v2'?'ML Optuna v2':m==='current_deviation_next_stop_eta'?'текущее отклонение':m||'нет метода';
    const etaView=v=>{const [label,glyph,col]=RISK[v.next_stop_risk_level];const delay=v.next_stop_predicted_delay_min;return {route:v.route_id,typeLabel:'Тип ТС неизвестен',bort:v.vehicle_id,probTxt:'—',glyph,riskLabel:label,col:V(col),lateTxt:`${delay>0?'+':''}${fmt(delay)} мин`,cdTxt:`ETA ${hm(v.next_stop_eta)}`,cdSub:`план ${hm(v.next_stop_planned_time)} · ${countdown(v.next_stop_planned_time)}`,issued:`оценка ${hm(v.next_stop_prediction_issued_at)} · ${methodLabel(v.next_stop_prediction_method)}`,reason:`Следующая остановка: ${v.next_stop}`,bg:V('p1'),ring:'none',hist:v.is_stale||v.source==='historical',open:()=>this.setState({drawer:v.vehicle_id,mapFocus:{route:v.route_id,token:Date.now()}})};};
    const route=S.plans[S.route],vs=Object.values(S.vehicles).filter(v=>v.route_id===S.route);
    const vRows=vs.map(v=>{const [label,glyph,col]=RISK[v.next_stop_risk_level];return {bort:v.vehicle_id,where:v.next_stop||'Положение на плане неизвестно',whereFull:v.segment_name||'—',arrow:'→',dev:`${fmt(v.deviation_now_min)} мин`,devCol:V('tx'),fc:v.next_stop_eta?`${hm(v.next_stop_eta)} (${v.next_stop_predicted_delay_min>0?'+':''}${fmt(v.next_stop_predicted_delay_min)} мин)`:'—',fcCol:V(col),spd:`${fmt(v.speed_kmh)} км/ч`,spdCol:V('tx'),idle:v.dwell_sec==null?'—':`${fmt(v.dwell_sec,0)} с`,doors:'нет данных',col:V(col),glyph,riskLabel:v.is_stale?'Устарело':v.next_stop_predicted_delay_min==null?'Нет ETA':label,cur:'default',open:()=>{}};});
    const selected=S.vehicles[S.drawer];let d={rec0:{},recRest:[],days:[]};
    if(selected){const v=selected,av=etaView(v),isMl=v.next_stop_prediction_method==='optuna-features-v2';d={...av,soft:V(v.next_stop_risk_level==='high'?'rs':'ys'),district:'исторический replay',at:hm(v.next_stop_eta),infoTitle:`Метод: ${methodLabel(v.next_stop_prediction_method)}`,infoSub:`обновлено ${hm(v.next_stop_prediction_issued_at)}`,ev:`План ${hm(v.next_stop_planned_time)}; ETA ${hm(v.next_stop_eta)}; оценка опоздания ${v.next_stop_predicted_delay_min>0?'+':''}${fmt(v.next_stop_predicted_delay_min)} мин.`,seg:v.segment_name||'Сегмент неизвестен',segMap:null,chartEl:h('p',{style:{padding:12,color:V('t2')}},isMl?`Оценка ${methodLabel(v.next_stop_prediction_method)} целевого события совпала со следующей остановкой.`:'ETA получено как плановое время плюс последнее доступное текущее отклонение; это не ML-прогноз.'),patLabel:v.next_stop_prediction_method,rec0:{title:'Открыть план ТС',bd:V('b'),bg:V('bs'),stCol:V('b'),status:'следующая остановка',effect:v.next_stop,onClick:()=>this.selectRoute(v.route_id)},recRest:[],histText:`Источник: ${v.source}. ${v.is_stale?'Позиция устарела.':''}`,days:[],canRoute:true};}
    const chip=(value,label)=>({label,bg:S.risk===value?V('p2'):'transparent',col:V('tx'),onClick:()=>this.setState({risk:value})});
    const modes=[['reserve','Выпустить резерв','Опоздание текущего ТС не меняется; интервалы неизвестны'],['reduce_dwell','Сократить будущую стоянку','Условно до 2 минут; не возвращает уже прошедшее время'],['hold','Удержать ТС','Добавить 2 минуты к прибытию выбранного ТС']];
    const scenario=S.scenario,lead=sum.mean_lead_time_min;
    return {rootVars,clock:hm(S.status.virtual_time),dateLabel:`${(S.status.virtual_time||'').slice(0,10)} · replay ×${fmt(S.status.speed,0)}`,
      themeLabel:S.theme==='dark'?'Светлая тема':'Тёмная тема',toggleTheme:()=>this.setState({theme:S.theme==='dark'?'light':'dark',menu:false}),toggleMenu:()=>this.setState({menu:!S.menu}),menuOpen:S.menu,menuBd:V('ln2'),roleLabel:'Демо · данные раздачи',
      tabs:[['overview','Обзор сети'],['route','План ТС']].map(([k,label])=>({label,bd:S.screen===k?V('b'):'transparent',col:V('tx'),onClick:()=>this.setState({screen:k})})),
      st:{dot:V(stale?'y':'g'),text:!S.connected?'Backend недоступен':S.status.replay_finished?'Replay завершён':S.status.stream_connected?'Поток онлайн':'Нет связи с NDTP',sub:`· ML ${S.status.ml_available?'Optuna v2':'fallback'}`,bd:V('ln2'),bg:'transparent'},onTele:()=>this.control(),degraded:stale,stale,retryIn:'авто',kvCol:V('tx'),
      statusDetail:S.error||(!S.connected?'Нет WebSocket: показано последнее полученное состояние.':S.status.detail||'Ожидание подключения'),
      showSkeleton:false,showOverview:S.screen==='overview',showRoute:S.screen==='route',skel5:[],
      kpi:{total:sum.vehicles_total??0,fresh:sum.vehicles_fresh??0,sched:sum.on_schedule_percent==null?'—':fmt(sum.on_schedule_percent)+'%',lateNow:`${sum.late_now??0} ТС · ${sum.deviations_known??0} известных отклонений`,fc:etaAll.length,hi:etaAll.filter(v=>v.next_stop_risk_level==='high').length,mid:etaAll.filter(v=>v.next_stop_risk_level==='risk').length,first:etaAll.length?countdown([...etaAll].sort((a,b)=>sec(a.next_stop_planned_time)-sec(b.next_stop_planned_time))[0].next_stop_planned_time):'—',inc:sum.alert_precision==null?'—':fmt(sum.alert_precision*100)+'%',incSub:`lead time ${fmt(lead)} мин`,mae:fmt(sum.forecast_mae_min),evaluated:sum.evaluated_predictions??0},
      q:S.q,onQ:e=>this.setState({q:e.target.value}),typeChips:[{label:'Тип ТС неизвестен',bg:'transparent',col:V('t2'),onClick:()=>{}}],riskChips:[chip('all','Любой риск'),chip('high','▲ >5 мин'),chip('risk','◆ >2 мин')],modeChips:[],isSchema:true,isGeo:false,frameRef:null,mapTitle:'',zoomIn:()=>this.activeMap?.getView().setZoom(this.activeMap.getView().getZoom()+1),zoomOut:()=>this.activeMap?.getView().setZoom(this.activeMap.getView().getZoom()-1),shownLabel:`${filtered.length} ТС`,mapEl:this.map(C),
      topSegs:[...etaAll.reduce((m,v)=>{const k=v.segment_id||v.vehicle_id,o=m.get(k)||{route:v.route_id,seg:v.segment_name||v.next_stop,full:v.segment_name||v.next_stop,cause:'ETA следующей остановки >2 мин',n:0,glyph:'◆',col:V('y'),spark:null,bd:V('ln'),bg:V('p1'),onClick:()=>this.selectRoute(v.route_id)};o.n++;m.set(k,o);return m;},new Map()).values()].sort((a,b)=>b.n-a.n).slice(0,5),alerts:filtered.map(etaView),feedEmpty:filtered.length===0,
      drawerOpen:!!selected,d,closeDrawer:()=>this.setState({drawer:null}),openWhatIf:()=>selected&&this.selectRoute(selected.route_id),markFalse:()=>this.setState({drawer:null}),goOverview:()=>this.setState({screen:'overview'}),
      routeLabel:route?.name||'План ТС',routeDescription:route?`${route.stops_count} плановых событий · тип и маршрут неизвестны`:'Ожидание плана',
      routeSelector:h('select',{value:S.route||'',onChange:e=>this.selectRoute(e.target.value),style:{background:V('p1'),color:V('tx'),padding:8,border:`1px solid ${V('ln2')}`,borderRadius:5}},S.routes.map(r=>h('option',{key:r.route_id,value:r.route_id},r.name))),
      routeStats:[{l:'Интервалы',v:'нет реестра',col:V('t2')},{l:'ТС в плане',v:vs.length,col:V('tx')},{l:'ETA >2 мин',v:etaAll.filter(v=>v.route_id===S.route).length,col:V('y')}],
      schemaEl:this.map(C,true),chartEl:this.chart(C),showReserve:false,vRows,toggleRows:()=>{},rowsLabel:`${vRows.length} ТС`,tableHint:'неизвестные измерения — прочерк',
      wiOpts:modes.map(([k,label,sub])=>({label,sub,bd:V(S.wi===k?'b':'ln2'),bg:S.wi===k?V('bs'):'transparent',dot:S.wi===k?V('b'):'transparent',onClick:()=>this.setState({wi:k,scenario:null})})),showTimes:false,wiTimes:[],
      wiRows:scenario?[{l:'Прогноз опоздания выбранного ТС',b:fmt(scenario.before.predicted_delay_min)+' мин',a:fmt(scenario.after.predicted_delay_min)+' мин',d:'условно'},{l:'Максимальный интервал',b:'нет данных',a:'нет данных',d:'—'}]:[],wiBefore:[],wiAfter:[],scenarioNotes:scenario?scenario.assumptions.join(' '):'Выберите действие и нажмите «Рассчитать сценарий». Это не команда транспорту.',applyLabel:'Рассчитать сценарий',applyWi:()=>this.scenario(),resetWi:()=>this.setState({scenario:null}),hasToast:!!S.error,toast:{title:'Состояние подключения',text:S.error||''},closeToast:()=>this.setState({error:null})};
  }
}
