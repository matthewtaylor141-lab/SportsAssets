/* BETTOR HQ2 · real Three.js operating floor.
   No duplicated/recoloured humanoid avatars. Seven unique desks/offices are
   represented as architectural presences until genuinely unique digital-human
   assets exist. State motion is driven only by durable floor payloads. */
import * as THREE from './team-demo/assets/three.module.min.js';

const world = document.querySelector('.hq2-world');
if (world && !matchMedia('(max-width:780px)').matches) {
  const mount = document.createElement('div');
  mount.className = 'hq2-webgl-layer';
  world.prepend(mount);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x030914, 0.045);
  const camera = new THREE.PerspectiveCamera(44, 1, 0.1, 100);
  camera.position.set(0, 8.8, 13.8);
  camera.lookAt(0, 0.6, 0);

  const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true, powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.65));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = false;
  mount.appendChild(renderer.domElement);
  world.classList.add('hq2-webgl');

  scene.add(new THREE.HemisphereLight(0x7ea7ff, 0x030712, 0.55));
  const key = new THREE.DirectionalLight(0x9bc3ff, 1.7); key.position.set(-4,8,8); scene.add(key);
  const rim = new THREE.PointLight(0x3157ff, 18, 24, 2); rim.position.set(0,3,-1); scene.add(rim);
  const cyan = new THREE.PointLight(0x57d8ff, 8, 18, 2); cyan.position.set(-6,2,2); scene.add(cyan);
  const green = new THREE.PointLight(0x58e3b2, 7, 16, 2); green.position.set(6,2,2); scene.add(green);

  const floorMat = new THREE.MeshStandardMaterial({color:0x040b16, roughness:0.82, metalness:0.25});
  const floor = new THREE.Mesh(new THREE.CircleGeometry(12, 96), floorMat);
  floor.rotation.x = -Math.PI/2; floor.position.y = -0.02; scene.add(floor);

  function ring(r, color, opacity) {
    const g = new THREE.RingGeometry(r-0.012, r+0.012, 128);
    const m = new THREE.MeshBasicMaterial({color,transparent:true,opacity,side:THREE.DoubleSide});
    const x = new THREE.Mesh(g,m); x.rotation.x=-Math.PI/2; x.position.y=0.012; scene.add(x); return x;
  }
  [2.3,4.5,6.8,9.2].forEach((r,i)=>ring(r,[0x3157ff,0x3157ff,0x57d8ff,0x3157ff][i],.08+i*.015));

  // radial aisle lines
  const lineMat = new THREE.LineBasicMaterial({color:0x3157ff,transparent:true,opacity:.10});
  for(let i=0;i<24;i++){
    const a=(i/24)*Math.PI*2;
    const pts=[new THREE.Vector3(Math.cos(a)*2.4,.02,Math.sin(a)*2.4),new THREE.Vector3(Math.cos(a)*10.4,.02,Math.sin(a)*10.4)];
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),lineMat));
  }

  // capital core
  const core = new THREE.Group();
  const coreBase = new THREE.Mesh(new THREE.CylinderGeometry(2.0,2.25,.34,64),new THREE.MeshStandardMaterial({color:0x07152d,metalness:.65,roughness:.34}));
  coreBase.position.y=.16; core.add(coreBase);
  const coreRing = new THREE.Mesh(new THREE.TorusGeometry(1.75,.055,12,96),new THREE.MeshBasicMaterial({color:0x5b78ff,transparent:true,opacity:.82}));
  coreRing.rotation.x=Math.PI/2;coreRing.position.y=.52;core.add(coreRing);
  const coreGlow = new THREE.Mesh(new THREE.CylinderGeometry(.86,1.32,2.5,48,1,true),
    new THREE.MeshBasicMaterial({color:0x3157ff,transparent:true,opacity:.055,side:THREE.DoubleSide,depthWrite:false}));
  coreGlow.position.y=1.65;core.add(coreGlow);
  const holo = new THREE.Mesh(new THREE.OctahedronGeometry(.52,2),new THREE.MeshStandardMaterial({color:0xaac2ff,emissive:0x3157ff,emissiveIntensity:1.7,roughness:.18,metalness:.2,transparent:true,opacity:.88}));
  holo.position.y=2.1;core.add(holo);
  scene.add(core);

  const seatMeta = (window.BTFloor && window.BTFloor.SEATS) || [];
  const radius = 7.15;
  const angles = [-2.55,-2.05,-1.52,-.92,-.34,.28,.92];
  const deskGroups = new Map();
  const pickables = [];

  function colorOf(hex){return new THREE.Color(hex||'#5b78ff');}
  function labelTexture(name, role, state, accent) {
    const c=document.createElement('canvas'); c.width=512;c.height=256; const x=c.getContext('2d');
    x.fillStyle='#06101f';x.fillRect(0,0,c.width,c.height);
    const g=x.createLinearGradient(0,0,c.width,0);g.addColorStop(0,accent);g.addColorStop(.2,'rgba(0,0,0,0)');
    x.fillStyle=g;x.fillRect(0,0,c.width,6);
    x.fillStyle='#f4f6fb';x.font='600 54px Arial';x.fillText(name,30,78);
    x.fillStyle='#7f94ac';x.font='500 25px Arial';x.fillText(role,30,118);
    x.fillStyle=accent;x.font='700 22px monospace';x.fillText(String(state||'UNKNOWN').replaceAll('_',' '),30,178);
    x.fillStyle='#52667f';x.font='500 17px monospace';x.fillText('BETTOR COMMAND · LIVE DESK',30,218);
    const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return t;
  }

  function buildDesk(seat, idx) {
    const a=angles[idx], group=new THREE.Group(); group.userData.slug=seat.slug;
    group.position.set(Math.cos(a)*radius,0,Math.sin(a)*radius+1.2);
    group.rotation.y=-a-Math.PI/2;
    // office halo
    const halo = new THREE.Mesh(new THREE.RingGeometry(1.22,1.27,64),new THREE.MeshBasicMaterial({color:colorOf(seat.accent),transparent:true,opacity:.32,side:THREE.DoubleSide}));
    halo.rotation.x=-Math.PI/2;halo.position.y=.025;group.add(halo);group.userData.halo=halo;
    // desk
    const desk = new THREE.Mesh(new THREE.BoxGeometry(2.2,.15,.72),new THREE.MeshStandardMaterial({color:0x091729,metalness:.72,roughness:.26}));
    desk.position.set(0,.86,.12);group.add(desk);
    const under = new THREE.Mesh(new THREE.BoxGeometry(1.76,.72,.10),new THREE.MeshStandardMaterial({color:0x06101f,metalness:.52,roughness:.38}));
    under.position.set(0,.48,.22);group.add(under);
    // screen
    const mat = new THREE.MeshBasicMaterial({map:labelTexture(seat.name,seat.short,'READING',seat.accent),toneMapped:false});
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.55,.77),mat);screen.position.set(0,1.47,-.05);screen.rotation.x=-.10;group.add(screen);group.userData.screen=screen;
    // presence: elegant light sculpture, not a fake human clone
    const head = new THREE.Mesh(new THREE.SphereGeometry(.19,24,16),new THREE.MeshStandardMaterial({color:0xe6edf8,emissive:colorOf(seat.accent),emissiveIntensity:.15,roughness:.35,metalness:.05}));
    head.position.set(0,1.82,.55);group.add(head);
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(.28,.72,8,16),new THREE.MeshStandardMaterial({color:0x0c1e35,emissive:colorOf(seat.accent),emissiveIntensity:.08,roughness:.4,metalness:.32}));
    body.position.set(0,1.22,.55);group.add(body);group.userData.presence=body;
    // point status light
    const lamp=new THREE.PointLight(colorOf(seat.accent),2.2,3.5,2);lamp.position.set(0,1.1,.15);group.add(lamp);group.userData.lamp=lamp;
    scene.add(group);deskGroups.set(seat.slug,group);pickables.push(desk,screen,body,head);
    [desk,screen,body,head].forEach(m=>m.userData.slug=seat.slug);
  }
  seatMeta.forEach(buildDesk);

  // collaboration tubes
  const links = new THREE.Group(); scene.add(links);
  function edgeCurve(from,to,color){
    const A=deskGroups.get(from),B=deskGroups.get(to);if(!A||!B)return;
    const p1=A.position.clone();p1.y=.12;const p2=B.position.clone();p2.y=.12;
    const mid=p1.clone().lerp(p2,.5);mid.y=.46;
    const curve=new THREE.QuadraticBezierCurve3(p1,mid,p2);
    const geom=new THREE.TubeGeometry(curve,24,.015,6,false);
    const mat=new THREE.MeshBasicMaterial({color,transparent:true,opacity:.28});
    links.add(new THREE.Mesh(geom,mat));
  }

  const ray = new THREE.Raycaster(), mouse=new THREE.Vector2();
  renderer.domElement.addEventListener('click',ev=>{
    const r=renderer.domElement.getBoundingClientRect();mouse.x=((ev.clientX-r.left)/r.width)*2-1;mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
    ray.setFromCamera(mouse,camera);const hit=ray.intersectObjects(pickables,false)[0];if(hit&&hit.object.userData.slug){
      window.dispatchEvent(new CustomEvent('bt:hq2floor:select',{detail:{slug:hit.object.userData.slug}}));
    }
  });

  // restrained orbit: drag to look, wheel to zoom
  let dragging=false,px=0,py=0,yaw=0,pitch=.17,dist=16.4;
  renderer.domElement.addEventListener('pointerdown',e=>{dragging=true;px=e.clientX;py=e.clientY;renderer.domElement.setPointerCapture(e.pointerId);});
  renderer.domElement.addEventListener('pointermove',e=>{if(!dragging)return;yaw-=(e.clientX-px)*.004;pitch=Math.max(-.03,Math.min(.48,pitch+(e.clientY-py)*.0028));px=e.clientX;py=e.clientY;});
  renderer.domElement.addEventListener('pointerup',()=>dragging=false);
  renderer.domElement.addEventListener('wheel',e=>{dist=Math.max(12,Math.min(20,dist+e.deltaY*.008));},{passive:true});

  let latest=null,last=performance.now(),reduced=matchMedia('(prefers-reduced-motion:reduce)').matches;
  window.addEventListener('bt:hq2floor:update',e=>{
    latest=e.detail||null;
    // update collaboration paths
    while(links.children.length) links.remove(links.children[0]);
    (latest&&latest.edges||[]).slice(-14).forEach(ed=>{
      const fs=(window.BTFloor.BY_AGENT[ed.from]||{});edgeCurve(fs.slug, (window.BTFloor.BY_AGENT[ed.to]||{}).slug, colorOf(fs.accent||'#3157ff'));
    });
    (latest&&latest.agents||[]).forEach(a=>{
      const g=deskGroups.get(a.slug);if(!g)return;const seat=window.BTFloor.BY_SLUG[a.slug];const active=/WORKING_ON|REVIEWING|CHALLENGING/.test(a.state||'');
      if(g.userData.screen){const old=g.userData.screen.material.map;g.userData.screen.material.map=labelTexture(seat.name,seat.short,a.state||'UNKNOWN',seat.accent);g.userData.screen.material.needsUpdate=true;old&&old.dispose();}
      g.userData.active=active;g.userData.lamp.intensity=active?4.1:(a.state==='STALE'||a.state==='NOT_DEPLOYED'?.45:1.8);
      g.userData.halo.material.opacity=active?.56:.22;
    });
  });

  function resize(){
    const r=mount.getBoundingClientRect();if(!r.width||!r.height)return;
    renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(mount);resize();

  function loop(now){
    requestAnimationFrame(loop);const dt=Math.min(.05,(now-last)/1000);last=now;
    if(document.hidden)return;
    const cx=Math.sin(yaw)*Math.cos(pitch)*dist, cz=Math.cos(yaw)*Math.cos(pitch)*dist, cy=6.9+Math.sin(pitch)*7;
    camera.position.lerp(new THREE.Vector3(cx,cy,cz+1.0),.075);camera.lookAt(0,.85,.4);
    if(!reduced){coreRing.rotation.z+=dt*.23;holo.rotation.y+=dt*.36;holo.rotation.x+=dt*.12;
      deskGroups.forEach(g=>{if(g.userData.active){g.userData.presence.position.y=1.22+Math.sin(now*.003+g.position.x)*.018;}});
    }
    renderer.render(scene,camera);
  }
  requestAnimationFrame(loop);
}
