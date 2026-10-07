/* Desk finish upgrade. Presentation only; no data, animation or order path.
 * Existing licensed avatars, state lights, cameras and interaction stay intact.
 * Textures are generated once, at a bounded resolution, without remote assets.
 */
export function makeDeskFinish(THREE, {phone=false, documentRef=document}={}) {
  const canvas=documentRef.createElement('canvas');
  canvas.width=phone?256:512; canvas.height=phone?128:256;
  const ctx=canvas.getContext('2d');
  if(!ctx) return new THREE.MeshStandardMaterial({color:'#6c5240',roughness:.43,metalness:.03});
  const w=canvas.width,h=canvas.height;
  const base=ctx.createLinearGradient(0,0,0,h);
  base.addColorStop(0,'#795e45');base.addColorStop(.45,'#a08361');base.addColorStop(1,'#65503d');
  ctx.fillStyle=base;ctx.fillRect(0,0,w,h);
  // Deterministic natural grain; never interpreted as market movement.
  for(let y=0;y<h;y+=2){
    ctx.beginPath();ctx.moveTo(0,y);
    for(let x=0;x<=w;x+=8){const dy=Math.sin(x*.017+y*.07)*1.8+Math.sin(x*.041+y*.12)*.6;ctx.lineTo(x,y+dy);}
    ctx.strokeStyle=y%7===0?'rgba(29,20,12,.16)':'rgba(235,212,178,.09)';ctx.lineWidth=.7;ctx.stroke();
  }
  const texture=new THREE.CanvasTexture(canvas);
  texture.colorSpace=THREE.SRGBColorSpace;
  texture.wrapS=texture.wrapT=THREE.RepeatWrapping;
  texture.repeat.set(1.2,1);
  return new THREE.MeshPhysicalMaterial({color:'#cfbb99',map:texture,
    roughness:.39,metalness:.025,clearcoat:phone ? 0.08 : 0.22,clearcoatRoughness:.32});
}
