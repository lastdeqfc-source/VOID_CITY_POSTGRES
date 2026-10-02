(() => {
'use strict';
const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();

let me = null;
const content = document.getElementById('content');

function headers(){ const d=tg?.initData||''; return d ? {'X-Telegram-Init-Data':d} : {}; }
async function api(path, options={}) {
  const r=await fetch(path,{...options,headers:{'Accept':'application/json',...headers(),...(options.headers||{})},cache:'no-store'});
  if(!r.ok){ if(r.status===401) throw Error('Открой игру через Telegram'); throw Error(await r.text()); }
  return r.json();
}
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function render(){
  const p=me.player;
  document.getElementById('coins').textContent='🪙 '+p.coins;
  content.innerHTML=`
  <section class="hero"><h1>Привет, ${esc(p.first_name||'игрок')}</h1><p>Уровень ${p.level} · XP ${p.xp}</p></section>
  <div class="grid">
    <button onclick="daily()">🎁 День</button><button onclick="buildings()">🏙 Город</button>
    <button onclick="quests()">📜 Квесты</button><button onclick="profile()">👤 Профиль</button>
    <button onclick="story()">📖 Сюжет</button><button onclick="training()">⚔️ Тренировка</button>
  </div>
  <div class="card"><b>Сохранение</b><p>Данные привязаны к Telegram ID и хранятся в PostgreSQL.</p></div>`;
}
async function reload(){ me=await api('/api/me'); render(); }
window.daily=async()=>{await api('/api/daily',{method:'POST'}); await reload();};
window.buildings=()=>{content.innerHTML=`<button class="back" onclick="render()">← Назад</button><h2>🏙 Город</h2>${me.buildings.map(b=>`<div class="card"><b>${esc(b.kind)}</b><p>Уровень ${b.level}</p><button onclick="collect('${b.kind}')">Собрать</button> <button onclick="upgrade('${b.kind}')">Улучшить</button></div>`).join('')}`};
window.collect=async k=>{await api('/api/collect/'+k,{method:'POST'});await reload();buildings();};
window.upgrade=async k=>{await api('/api/upgrade/'+k,{method:'POST'});await reload();buildings();};
window.quests=()=>{content.innerHTML=`<button class="back" onclick="render()">← Назад</button><h2>📜 Квесты</h2>${me.quests.map(q=>`<div class="card"><b>${esc(q.key)}</b><p>Прогресс: ${q.progress}</p><button onclick="claim('${q.key}')">Забрать</button></div>`).join('')}`};
window.claim=async k=>{await api('/api/quest/'+k+'/claim',{method:'POST'});await reload();quests();};
window.profile=()=>{const p=me.player;content.innerHTML=`<button class="back" onclick="render()">← Назад</button><h2>👤 Профиль</h2><div class="card"><p>ID: ${p.id}</p><p>Имя: ${esc(p.first_name)}</p><p>Уровень: ${p.level}</p><p>Хранилище: PostgreSQL</p></div>`};
window.story=()=>{content.innerHTML=`<button class="back" onclick="render()">← Назад</button><h2>📖 Первый сигнал</h2><div class="card"><p>Город просыпается, а на старом терминале появляется неизвестный сигнал.</p><button onclick="alert('Новая глава скоро будет доступна')">Продолжить</button></div>`};
window.training=async()=>{const r=await api('/api/pvp/training',{method:'POST'});alert(r.message+' +'+r.reward_xp+' XP');await reload();};
reload().catch(e=>{content.innerHTML=`<div class="card"><h2>⚠️ Ошибка</h2><p>${esc(e.message)}</p><button onclick="location.reload()">Повторить</button></div>`});
})();