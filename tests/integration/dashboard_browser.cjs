// npm install --no-save playwright-core
// DASHBOARD_URL=http://127.0.0.1:8080 node tests/integration/dashboard_browser.cjs
// Uses installed Chrome, or CHROMIUM_PATH for another Chromium binary.
const assert=require('node:assert/strict');
const {chromium}=require('playwright-core');
(async()=>{
  const browser=await chromium.launch(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  // Record native map instances only inside this test to inspect real camera/layer state.
  await page.route('**/vendor/ol.js',async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:(await response.text())+'\nwindow.testMaps=[];const NativeMap=ol.Map;ol.Map=class extends NativeMap{constructor(options){super(options);window.testMaps.push(this)}};'});
  });
  try{
    await page.goto(process.env.DASHBOARD_URL||'http://127.0.0.1:8080');
    await page.waitForFunction(()=>document.querySelector('header')?.textContent.includes('Поток онлайн'));
    await page.waitForSelector('.vehicle-marker');
    await page.waitForFunction(()=>testMaps.at(-1).getLayers().item(1).getSource().getFeatures().length>20);
    const waitForStreet=()=>page.waitForFunction(()=>{const c=document.querySelector('.street-tiles canvas');return c?.width>0&&c.getContext('2d').getImageData(c.width/2,c.height/2,1,1).data[3]>0});
    await waitForStreet();
    const count=await page.evaluate(()=>testMaps.at(-1).getLayers().item(1).getSource().getFeatures().length);
    await page.getByRole('button',{name:'✓ Планы',exact:true}).click();
    await page.waitForFunction(()=>testMaps.at(-1).getLayers().item(1).getSource().getFeatures().length===0);
    await page.getByRole('button',{name:'Планы',exact:true}).click();
    await page.waitForFunction(n=>testMaps.at(-1).getLayers().item(1).getSource().getFeatures().length>=n,count);
    await page.getByRole('button',{name:'Приблизить карту',exact:true}).click();
    const camera=await page.evaluate(()=>({center:testMaps.at(-1).getView().getCenter(),zoom:testMaps.at(-1).getView().getZoom(),maps:testMaps.length}));
    await page.waitForTimeout(2500);
    assert.deepEqual(await page.evaluate(()=>({center:testMaps.at(-1).getView().getCenter(),zoom:testMaps.at(-1).getView().getZoom(),maps:testMaps.length})),camera,'stream preserves map and camera');
    await page.getByRole('button',{name:'Отдалить карту',exact:true}).click();
    await page.locator('.vehicle-marker').first().click();
    await page.waitForSelector('select');
    assert.equal(await page.locator('select option').count(),13);
    await page.getByRole('button',{name:'Рассчитать сценарий',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('aside[data-screen-label="What-if"]')?.textContent.includes('Резерв не уменьшает опоздание'));
    assert.equal(await page.evaluate(()=>typeof window.L),'undefined');
    assert.ok(!requests.some(url=>/leaflet/i.test(url)),'no old library requests');
    assert.deepEqual(errors,[]);
    await waitForStreet();
    if(process.env.SCREENSHOT_PATH)await page.screenshot({path:process.env.SCREENSHOT_PATH,fullPage:true});
    console.log(JSON.stringify({result:'passed',map:'OpenLayers',plans:count,route_selector:13,camera:'preserved during live updates',markers:'click opens plan',whatif:'rendered',runtime_errors:errors},null,2));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
