/** New daylit office. Uses the repository's exact licensed avatar identities.
 * Lazy loaded, no network API calls, no inferred staff motion or execution writes.
 * Geometry/materials are new; no reuse of the dark HQ6 room composition.
 */
export async function mountOffice(host,agents,onPick){
 const THREE=await import('/team-demo/assets/three.module.min.js');
 const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});
 renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));renderer.setClearColor('#e9f0f4');
 renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.35;
 renderer.shadowMap.enabled=host.clientWidth>650;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
 const scene=new THREE.Scene();scene.background=new THREE.Color('#e9f0f4');scene.fog=new THREE.Fog('#e9f0f4',28,65);
 const camera=new THREE.PerspectiveCamera(42,1,.1,100),target=new THREE.Vector3(0,1.1,0);
 let yaw=.16,elev=.53,radius=23,disposed=false,paused=false,raf=0;
 scene.add(new THREE.HemisphereLight('#f4fbff','#c9c3b2',2.5));
 const sun=new THREE.DirectionalLight('#fff5e1',3);sun.position.set(-9,14,8);sun.castShadow=renderer.shadowMap.enabled;sun.shadow.mapSize.set(1024,1024);sun.shadow.camera.left=-16;sun.shadow.camera.right=16;sun.shadow.camera.top=14;sun.shadow.camera.bottom=-14;scene.add(sun);
 const fill=new THREE.DirectionalLight('#e5f2ff',1.3);fill.position.set(10,6,-8);scene.add(fill);
 const materials={floor:new THREE.MeshStandardMaterial({color:'#e2ded4',roughness:.85}),wall:new THREE.MeshStandardMaterial({color:'#eeefea',roughness:.8}),wood:new THREE.MeshStandardMaterial({color:'#cbbda7',roughness:.65}),desk:new THREE.MeshStandardMaterial({color:'#f0ede5',roughness:.4}),metal:new THREE.MeshStandardMaterial({color:'#63747e',metalness:.35,roughness:.45}),screen:new THREE.MeshStandardMaterial({color:'#244455',roughness:.38}),glass:new THREE.MeshStandardMaterial({color:'#bddae4',transparent:true,opacity:.22,roughness:.18}),chair:new THREE.MeshStandardMaterial({color:'#546c75',roughness:.8}),green:new THREE.MeshStandardMaterial({color:'#618a74',roughness:.85})};
 const pickables=[],boards=[],modelRoots=[];
 function box(w,h,d,x,y,z,m,parent=scene){const o=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),m);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;}
 box(20,.2,14,0,-.1,0,materials.floor);box(20,4.3,.15,0,2.1,-7,materials.wall);box(.12,4.4,14,-10,2.1,0,materials.glass);box(.12,4.4,14,10,2.1,0,materials.glass);
 for(let x=-8;x<=8;x+=4){box(.09,4.1,.11,x,2.1,-6.83,materials.metal);if(Math.abs(x)>2)box(3.85,3.5,.025,x+1.9,2.35,-6.88,materials.glass);}
 for(let z=-5;z<=5;z+=3){box(.09,4.2,.09,-9.92,2.1,z,materials.metal);box(.09,4.2,.09,9.92,2.1,z,materials.metal);}
 // Ceiling light rails, a low lounge wall, clear central aisle and plants.
 for(const x of [-6,0,6]){const m=new THREE.MeshStandardMaterial({color:'#fff8e9',emissive:'#fff4d8',emissiveIntensity:.55});box(.1,.06,10,x,4.5,0,m);}
 const grid=new THREE.GridHelper(20,20,'#c0c6c3','#cdd0ca');grid.position.y=.012;grid.material.transparent=true;grid.material.opacity=.30;scene.add(grid);
 function label(t,w=2.8,h=.64){const c=document.createElement('canvas');c.width=768;c.height=176;const ctx=c.getContext('2d');ctx.fillStyle='#f8fafb';ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#183b5b';ctx.font='600 48px system-ui';ctx.fillText(t,28,74);ctx.fillStyle='#8092a4';ctx.font='25px system-ui';ctx.fillText('BETTOR / RECORDED DESK',28,128);const tx=new THREE.CanvasTexture(c);tx.colorSpace=THREE.SRGBColorSpace;const mesh=new THREE.Mesh(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({map:tx,side:THREE.DoubleSide}));return {mesh,c,ctx,tx};}
 const branding=label('BETTORTOKEN',4.7,1.08);branding.mesh.position.set(0,3.15,-6.89);scene.add(branding.mesh);boards.push(branding);
 for(const x of [-9,9])for(const z of [-5.7,5.7]){const pot=new THREE.Mesh(new THREE.CylinderGeometry(.34,.25,.65,14),materials.wall);pot.position.set(x,.32,z);scene.add(pot);for(let i=0;i<5;i++){const leaf=new THREE.Mesh(new THREE.SphereGeometry(.35,12,10),materials.green);leaf.scale.set(.65,1.6,.8);leaf.position.set(x+Math.sin(i*1.3)*.22,1.05+i*.08,z+Math.cos(i*1.3)*.19);leaf.rotation.z=(i-2)*.16;scene.add(leaf);}}
 const coords=[[-6,-3],[-2,-3],[2,-3],[6,-3],[-6,2],[-2,2],[2,2],[6,2]];
 const CAST={derek:'derek',karen:'karen',scout:'scout',eddie:'eddie',allocator:'allie',audrey:'audrey',xavier:'xavier'};
 agents.forEach((a,i)=>{const[x,z]=coords[i],g=new THREE.Group();g.position.set(x,0,z);scene.add(g);box(3,.12,1.2,0,1.0,0,materials.desk,g);for(const px of [-1.2,1.2])box(.1,.93,.85,px,.46,0,materials.metal,g);box(.12,.34,.12,.6,1.22,-.15,materials.metal,g);box(.94,.56,.065,.6,1.57,-.15,materials.screen,g);box(.6,.035,.25,.6,1.1,.26,materials.metal,g);box(.85,.13,.82,-.5,.62,-.78,materials.chair,g);box(.86,.7,.12,-.5,.99,-1.13,materials.chair,g);
 const sign=label(a.name);sign.mesh.position.set(x,2.6,z-.65);scene.add(sign.mesh);sign.slug=a.slug;boards.push(sign);
 const hit=new THREE.Mesh(new THREE.BoxGeometry(3.1,3,2),new THREE.MeshBasicMaterial({visible:false}));hit.position.set(x,1.5,z-.25);hit.userData.slug=a.slug;scene.add(hit);pickables.push(hit);
 });
 host.appendChild(renderer.domElement);renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','3D virtual office. Arrow keys rotate the camera. Inspect agent buttons in Desk view for the accessible equivalent.');
 function draw(){raf=0;if(disposed||paused)return;camera.position.set(target.x+Math.sin(yaw)*radius*Math.cos(elev),target.y+Math.sin(elev)*radius,target.z+Math.cos(yaw)*radius*Math.cos(elev));camera.lookAt(target);renderer.render(scene,camera);}
 function request(){if(!raf&&!disposed&&!paused)raf=requestAnimationFrame(draw);}
 function resize(){if(disposed)return;const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h);camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();request();}
 const observer=new ResizeObserver(resize);observer.observe(host);resize();
 let drag=null;
 function down(e){drag={x:e.clientX,y:e.clientY,moved:false};renderer.domElement.setPointerCapture(e.pointerId);}
 function move(e){if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>2)drag.moved=true;yaw-=dx*.007;elev=Math.max(.20,Math.min(1.15,elev+dy*.004));drag.x=e.clientX;drag.y=e.clientY;request();}
 function up(e){if(drag&&!drag.moved){const r=renderer.domElement.getBoundingClientRect(),v=new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),ray=new THREE.Raycaster();ray.setFromCamera(v,camera);const hit=ray.intersectObjects(pickables)[0];if(hit)onPick(hit.object.userData.slug);}drag=null;}
 function wheel(e){e.preventDefault();radius=Math.max(9,Math.min(35,radius+e.deltaY*.014));request();}
 function key(e){if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','-'].includes(e.key)){e.preventDefault();if(e.key==='ArrowLeft')yaw-=.12;if(e.key==='ArrowRight')yaw+=.12;if(e.key==='ArrowUp')elev=Math.min(1.15,elev+.07);if(e.key==='ArrowDown')elev=Math.max(.20,elev-.07);if(e.key==='+')radius=Math.max(9,radius-1);if(e.key==='-')radius=Math.min(35,radius+1);request();}}
 renderer.domElement.addEventListener('pointerdown',down);renderer.domElement.addEventListener('pointermove',move);renderer.domElement.addEventListener('pointerup',up);renderer.domElement.addEventListener('wheel',wheel,{passive:false});renderer.domElement.addEventListener('keydown',key);
 function update(as){for(const b of boards){if(!b.slug)continue;const a=as.find(x=>x.slug===b.slug);if(!a)continue;b.ctx.fillStyle='#f8fafb';b.ctx.fillRect(0,0,768,176);b.ctx.fillStyle='#173a59';b.ctx.font='600 47px system-ui';b.ctx.fillText(a.name,27,73);b.ctx.fillStyle=a.state==='STALE'?'#9e6511':'#62768c';b.ctx.font='24px system-ui';b.ctx.fillText(a.state.replaceAll('_',' '),27,128);b.tx.needsUpdate=true;}request();}
 update(agents);
 const status=host.querySelector('.three-status');let loaded=0,failed=0;
 try{
  const [{GLTFLoader},{MeshoptDecoder},{armsDown}]=await Promise.all([import('/team-demo/assets/GLTFLoader.js'),import('/team-demo/assets/meshopt_decoder.module.js'),import('/team-demo/assets/cc_avatar.js')]);
  const loader=new GLTFLoader();loader.setMeshoptDecoder(MeshoptDecoder);
  for(let i=0;i<agents.length;i++){
   const a=agents[i],name=CAST[a.slug];if(!name||!a.registered)continue;
   try{const gltf=await loader.loadAsync(`/team-demo/assets/models/${name}.glb`);if(disposed)break;const root=gltf.scene;armsDown(root);root.updateMatrixWorld(true);const b=new THREE.Box3().setFromObject(root),s=new THREE.Vector3();b.getSize(s);const scale=1.7/Math.max(s.y,.001);root.scale.setScalar(scale);const[x,z]=coords[i];root.position.set(x-.65,-b.min.y*scale,z-.67);root.rotation.y=.10;root.traverse(o=>{if(o.isMesh){o.castShadow=renderer.shadowMap.enabled;o.frustumCulled=false;}});scene.add(root);modelRoots.push(root);loaded++;request();}catch{failed++;}
   if(status)status.textContent=`${loaded} repository avatars loaded${failed?' · '+failed+' assets unavailable':''}. Ariana is not substituted with another character.`;
  }
 }catch{if(status)status.textContent='Office geometry loaded. Licensed avatar files are unavailable; no substitute characters are fabricated.';}
 if(status&&loaded===0&&failed===0)status.textContent='No registered agent avatar was loaded. Use Desk view for source status.';
 return {update,pause(v){paused=v;if(!v)request();},dispose(){disposed=true;cancelAnimationFrame(raf);observer.disconnect();renderer.domElement.removeEventListener('pointerdown',down);renderer.domElement.removeEventListener('pointermove',move);renderer.domElement.removeEventListener('pointerup',up);renderer.domElement.removeEventListener('wheel',wheel);renderer.domElement.removeEventListener('keydown',key);scene.traverse(o=>{o.geometry?.dispose();if(o.material){for(const m of [].concat(o.material)){m.map?.dispose();m.dispose();}}});renderer.dispose();renderer.domElement.remove();}};
}
