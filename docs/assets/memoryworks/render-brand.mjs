import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(import.meta.url);
const {createCanvas,loadImage}=require('/Users/sanket/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@napi-rs/canvas');
for(const [source,target,width] of [
  ['memoryworks-brand-board.svg','memoryworks-brand-board.png',1600],
  ['memoryworks-lockup.svg','memoryworks-logo.png',2400],
  ['memoryworks-icon.svg','memoryworks-icon.png',1024],
  ['memoryworks-mark.svg','memoryworks-mark.png',1024],
]) {
  const img=await loadImage(fs.readFileSync(path.join(root,source)));
  const canvas=createCanvas(width,Math.round(width*img.height/img.width));
  canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);
  fs.writeFileSync(path.join(root,target),canvas.toBuffer('image/png'));
  console.log(target,canvas.width,canvas.height);
}
if(process.argv.includes('--install')) {
  const publicDir=path.resolve(root,'../../../frontend/public');
  const brandDir=path.join(publicDir,'memoryworks');
  fs.mkdirSync(brandDir,{recursive:true});
  for(const name of fs.readdirSync(root).filter(name=>/^memoryworks-.*\.(svg|png)$/.test(name)&&!name.includes('brand-board'))) {
    fs.copyFileSync(path.join(root,name),path.join(brandDir,name));
  }
  fs.copyFileSync(path.join(root,'memoryworks-logo.png'),path.join(publicDir,'logo.png'));
  fs.copyFileSync(path.join(root,'memoryworks-icon.png'),path.join(publicDir,'logo-icon.png'));
  const img=await loadImage(fs.readFileSync(path.join(root,'memoryworks-logo.png')));
  const canvas=createCanvas(img.width,img.height);
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#f5f9fa';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0);
  fs.writeFileSync(path.join(publicDir,'logo.jpg'),canvas.toBuffer('image/jpeg',95));
  console.log('Installed production logo assets.');
}
