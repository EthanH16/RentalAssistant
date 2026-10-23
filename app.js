const areaCenters = {
  'DTLA':[34.045,-118.255], 'Koreatown / Wilshire / Westlake':[34.061,-118.292],
  'Culver City / Westside / Silicon Beach':[34.025,-118.385], 'Santa Monica / Marina / UCLA-Westwood':[34.035,-118.435],
  'Hollywood / East Hollywood / Larchmont / West Hollywood':[34.095,-118.330],
  'Echo Park / Silver Lake / Sunset corridor':[34.084,-118.274],
  'Burbank / North Hollywood / Studio City':[34.165,-118.365], 'Glendale':[34.145,-118.235],
  'Pasadena / San Gabriel Valley / Monrovia / El Monte':[34.145,-118.085],
  'Long Beach / South Bay / Orange County':[33.825,-118.125], 'Inland Empire / Ventura / outer LA County':[34.060,-117.650],
  'Area pending normalization':[34.090,-118.220]
};

const roster = (window.__BUILDINGS__ || []).map(({name, area, source}) => [name, area, source]);

const known = {
  '825 South Hill':{price:3044,ref:'2B ~$3,044–$7,713',tags:['2B','Pet notes','High-rise'],concession:'1 month free on select units',profile:'../LA-Rental-Vault/02 Research/825 South Hill - DTLA.md',lat:34.0445,lng:-118.2565},
  'Hope + Flower':{price:3420,ref:'2B ~$3,420–$5,008',tags:['2B','Pet notes','Newer'],concession:'LL bonus varies',lat:34.0388,lng:-118.2655},
  'Olympic + Hill':{price:3522,ref:'2B ~$3,522–$5,233',tags:['2B','High-rise','Views'],concession:'Look & Lease on select 3B',lat:34.0433,lng:-118.2560},
  'Park Fifth':{price:3850,ref:'2B ~$3,850–$7,245',tags:['Gas stove','Valet','Rooftop pool'],concession:'Up to 8 weeks on select terms',profile:'../LA-Rental-Vault/02 Research/Park Fifth - DTLA.md',lat:34.0482,lng:-118.2510},
  'Circa':{price:4512,ref:'2B ~$4,512–$7,420',tags:['Crypto Arena','Parking','Pool'],concession:'Up to 6 weeks on select units',profile:'../LA-Rental-Vault/02 Research/Circa - DTLA.md',lat:34.0400,lng:-118.2680},
  'Thea at Metropolis':{price:4774,ref:'2B ~$4,774–$5,619',tags:['Pool','Downtown','Pet-friendly'],concession:'Up to 8 weeks + LL bonus',lat:34.0470,lng:-118.2630},
  'Opus LA':{price:5129,ref:'2B ~$5,129–$5,332',tags:['High-rise','Reference price'],profile:'../LA-Rental-Vault/02 Research/Opus LA - Koreatown.md',lat:34.0610,lng:-118.2960},
  'Kurve':{price:4190,ref:'2B ~$4,190–$4,903',tags:['Transit','Reference price'],concession:'Up to 6 weeks + LL',lat:34.0620,lng:-118.2860},
  'Lana':{price:5749,ref:'2B2B from ~$5,749',tags:['Culver City','Pet-friendly'],profile:'../LA-Rental-Vault/02 Research/Lana - Culver City.md',lat:34.0220,lng:-118.3960},
  'Habitat Residences':{price:4959,ref:'2B1B from ~$4,959',tags:['Newer','E Line','Pet-friendly'],concession:'Up to 10 weeks',lat:34.0240,lng:-118.3700},
  'Arya':{price:4995,ref:'2B ~$4,995–$6,200',tags:['Boutique','U4 transit'],concession:'LL + parking promo',lat:34.0230,lng:-118.4070},
  'VOX':{price:4257,ref:'2B B2 $4,257 base',tags:['E Line','Pet-friendly','Whole Foods'],concession:'5–6 weeks noted',profile:'../LA-Rental-Vault/02 Research/VOX - Culver City West Adams.md',lat:34.0250,lng:-118.3700},
  'West Edge':{price:4721,ref:'2B ~$4,721–$6,475',tags:['Westside','Reference price'],concession:'Up to 4 weeks',lat:34.025,lng:-118.411},
  'Millennium Santa Monica':{price:4655,ref:'2B ~$4,655–$4,925',tags:['Santa Monica','Reference price'],concession:'Included/self-amortized',lat:34.019,lng:-118.495},
  'Aston at Gateway':{tags:['SGV','Partner'],profile:'../LA-Rental-Vault/02 Research/Aston at Gateway - San Gabriel Valley.md',lat:34.145,lng:-118.080},
  'Serena Village':{tags:['Inland Empire','Partner'],profile:'../LA-Rental-Vault/02 Research/Serena Village - Inland Empire.md',lat:34.060,lng:-117.650}
};

const seenPerArea = {};
const buildings = roster.map(([name, area, rosterSource = 'Direct']) => {
  const meta = known[name] || {}, center = areaCenters[area] || areaCenters['Area pending normalization'];
  const i = seenPerArea[area] || 0; seenPerArea[area] = i + 1;
  const ring = Math.floor(i / 8) + 1, angle = (i % 8) * Math.PI / 4;
  // The normalized JSON roster is authoritative. Direct partnership wins
  // if a future source reconciliation produces the same canonical name.
  const source = rosterSource === 'Direct' ? 'Direct' : 'Niko';
  return {name, area, source, lat:meta.lat ?? center[0] + Math.sin(angle) * 0.004 * ring, lng:meta.lng ?? center[1] + Math.cos(angle) * 0.006 * ring, price:meta.price ?? null, ref:meta.ref ?? 'Reference profile / live price needed', tags:meta.tags ?? ['Partnership roster'], concession:meta.concession ?? null, profile:meta.profile};
});

const map = new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/positron',
  center:[-118.30,34.075],
  zoom:9,
  minZoom:8,
  maxZoom:17,
  maxBounds:[[-119.35,33.35],[-117.05,34.85]],
  maxBoundsViscosity:1.0,
  attributionControl:false
});
map.addControl(new maplibregl.NavigationControl({showCompass:true}), 'bottom-right');
map.addControl(new maplibregl.AttributionControl({customAttribution:'© OpenFreeMap © OpenMapTiles Data from OpenStreetMap'}), 'bottom-right');

const markers = new Map();
const areaFilter = document.querySelector('#areaFilter');
const cards = document.querySelector('#cards');
const search = document.querySelector('#search');
const minPrice = document.querySelector('#minPrice');
const maxPrice = document.querySelector('#maxPrice');
const hasConcession = document.querySelector('#hasConcession');
const visibleCount = document.querySelector('#visibleCount');
const resultMeta = document.querySelector('#resultMeta');
const sourceButtons = [...document.querySelectorAll('#sourceFilters button')];
let source = 'all';

[...new Set(buildings.map(b=>b.area))].sort().forEach(area => { const opt=document.createElement('option'); opt.value=area; opt.textContent=area; areaFilter.appendChild(opt); });

function colorFor(source) { return source === 'Niko' ? '#4e91bb' : '#145c4b'; }
function popup(b) { return `<div class="popup"><h3>${b.name}</h3><p>${b.area} · <strong>${b.source}</strong></p><p>${b.ref}</p>${b.concession ? `<p><strong>${b.concession}</strong></p>` : ''}</div>`; }
function matches(b) {
  const term = search.value.trim().toLowerCase();
  const low = Number(minPrice.value) || 0, high = Number(maxPrice.value) || Infinity;
  return (!term || `${b.name} ${b.area} ${b.tags.join(' ')}`.toLowerCase().includes(term)) &&
    (source === 'all' || b.source === source) &&
    (areaFilter.value === 'all' || b.area === areaFilter.value) &&
    (!minPrice.value || (b.price !== null && b.price >= low)) && (!maxPrice.value || (b.price !== null && b.price <= high)) &&
    (!hasConcession.checked || Boolean(b.concession));
}
function render() {
  const visible = buildings.filter(matches); visibleCount.textContent = visible.length; resultMeta.textContent = `${visible.length} / ${buildings.length}`;
  cards.innerHTML = visible.length ? visible.map((b,i)=>`<article class="card" data-name="${b.name}"><div class="card-top"><h3>${b.name}</h3><span class="badge ${b.source === 'Niko' ? 'niko' : ''}">${b.source}</span></div><div class="area">${b.area}</div><div class="card-bottom"><div class="price">${b.price ? '$'+b.price.toLocaleString() : '—'} <small>${b.price ? 'from / ref.' : 'price needed'}</small></div><div class="tags">${b.tags.slice(0,2).map(t=>`<span class="tag">${t}</span>`).join('')}</div></div></article>`).join('') : '<div class="empty">No buildings match these filters. Try widening the area or price range.</div>';
  buildings.forEach(b=>{ const m=markers.get(b.name); if (m) { if (visible.includes(b)) m.addTo(map); else m.remove(); } });
  document.querySelectorAll('.card').forEach(card=>card.addEventListener('click',()=>selectBuilding(card.dataset.name)));
}
function selectBuilding(name) { const b=buildings.find(x=>x.name===name); if (!b) return; map.flyTo({center:[b.lng,b.lat],zoom:14,duration:650}); const marker=markers.get(name); if (marker) marker.togglePopup(); document.querySelectorAll('.card').forEach(c=>c.classList.toggle('selected',c.dataset.name===name)); if (b.profile) window.open(b.profile,'_blank'); }
function iconFor(b) {
  const label = b.name.replace(/[^A-Za-z0-9]/g,'').slice(0,2).toUpperCase();
  const el=document.createElement('div'); el.className='building-pin-wrap'; el.innerHTML=`<div class="building-pin" style="--pin:${colorFor(b.source)}"><span>${label}</span></div>`; return el;
}
Object.entries(areaCenters).forEach(([area, point]) => {
  const el=document.createElement('div'); el.className='area-label-wrap'; el.innerHTML=`<div class="area-label">${area.split(' / ')[0]}</div>`;
  new maplibregl.Marker({element:el,anchor:'center'}).setLngLat([point[1],point[0]]).addTo(map);
});
buildings.forEach(b=>{ const marker=new maplibregl.Marker({element:iconFor(b),anchor:'bottom'}).setLngLat([b.lng,b.lat]).setPopup(new maplibregl.Popup({offset:32,maxWidth:'280px'}).setHTML(popup(b))).addTo(map); markers.set(b.name,marker); });
sourceButtons.forEach(btn=>btn.addEventListener('click',()=>{source=btn.dataset.source; sourceButtons.forEach(x=>x.classList.toggle('active',x===btn)); render();}));
[search,minPrice,maxPrice,hasConcession,areaFilter].forEach(el=>el.addEventListener('input',render));
document.querySelector('#reset').addEventListener('click',()=>{search.value='';minPrice.value='';maxPrice.value='';hasConcession.checked=false;areaFilter.value='all';source='all';sourceButtons.forEach(x=>x.classList.toggle('active',x.dataset.source==='all'));render();});
render();
