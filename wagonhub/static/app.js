const app = document.querySelector('#app');
const state = { token: localStorage.getItem('wagonhub_token'), user: null, filters: {} };
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money = value => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value);
const roles = {track:'Владелец путей',wagon:'Владелец вагонов'};
const statuses = {available:'Доступно',withdrawn:'Снято',pending:'Ожидает решения',confirmed:'Подтверждено',rejected:'Отклонено'};
const toast = (message, error=false) => { const node=document.querySelector('#toast'); node.textContent=message; node.className=(error?'error ':'')+'show'; clearTimeout(toast.timer); toast.timer=setTimeout(()=>node.className='',4000); };
async function api(path, options={}) {
  const headers = {...options.headers};
  if (state.token) headers.Authorization = 'Bearer '+state.token;
  if (options.body) headers['Content-Type']='application/json';
  let response;
  try { response = await fetch('/api'+path,{...options,headers}); }
  catch { throw new Error('Нет соединения с сервером'); }
  if (response.status===204) return null;
  const data=await response.json();
  if (!response.ok) {
    if (response.status===401 && path!=='/login') { state.token=null; state.user=null; localStorage.removeItem('wagonhub_token'); }
    const detail = data.detail;
    throw new Error(typeof detail==='string' ? detail : Array.isArray(detail) ? detail.map(x=>x.msg).join('; ') : 'Не удалось выполнить действие');
  }
  return data;
}
const send = (path, body) => api(path,{method:'POST',body:JSON.stringify(body)});
const values = form => Object.fromEntries(new FormData(form).entries());
function pageHeading(title, description) { return `<div class="page-heading"><h1>${title}</h1><p>${description}</p></div>`; }
function empty(title, description, action='') { return `<div class="empty"><div class="symbol">◇</div><h3>${title}</h3><p>${description}</p>${action}</div>`; }
function refreshNav() {
  const page=location.hash.slice(1).split('?')[0]||'listings';
  document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===page));
  document.querySelector('#create-link').hidden=state.user?.role!=='track';
  document.querySelector('#auth-actions').innerHTML=state.user ? `<button class="btn ghost small" id="logout">Выйти</button>` : `<a class="btn small" href="#login">Войти</a>`;
}
async function render() {
  refreshNav();
  const page=location.hash.slice(1).split('?')[0]||'listings';
  try {
    if (page==='listings') return await renderListings();
    if (page==='login'||page==='register') return renderAuth(page);
    if (!state.user) { location.hash='login'; return; }
    if (page==='account') return await renderAccount();
    if (page==='create'&&state.user.role==='track') return renderCreate();
    if (/^edit\/\d+$/.test(page)&&state.user.role==='track') return await renderEdit(Number(page.split('/')[1]));
    if (page==='company'&&state.user.role==='wagon') return await renderCompany();
    location.hash='listings';
  } catch(error) { app.innerHTML=empty('Не удалось загрузить страницу',escapeHtml(error.message),'<button class="btn" id="retry">Повторить</button>'); }
}
async function renderListings() {
  const qs=new URLSearchParams(Object.entries(state.filters).filter(([,v])=>v));
  const items=await api('/listings?'+qs);
  const city=escapeHtml(state.filters.city||'');
  app.innerHTML=`<section class="hero"><div class="hero-content"><div class="eyebrow">Инфраструктура рядом</div><h1>Место для ваших вагонов начинается здесь.</h1><p>Находите свободные пути, сравнивайте условия и отправляйте заявку владельцу площадки.</p><div class="hero-stat"><div><strong>${items.length}</strong> предложений по запросу</div><div><strong>24/7</strong> доступ к площадке</div></div></div></section>
  <div class="section-heading"><div><h2>Актуальные предложения</h2><p>Свободные вагоно-места от владельцев путей</p></div></div>
  <div class="layout"><aside class="panel"><h3>Найти площадку</h3><form id="filters"><div class="field"><label for="city">Город</label><input id="city" name="city" placeholder="Например, Москва" value="${city}"></div><div class="field"><label for="capacity">Количество вагонов от</label><input id="capacity" name="min_capacity" type="number" min="1" value="${escapeHtml(state.filters.min_capacity||'') }" placeholder="Любое"></div><div class="field"><label for="max_price">Цена за вагон до, ₽</label><input id="max_price" name="max_price" type="number" min="1" step="0.01" value="${escapeHtml(state.filters.max_price||'')}" placeholder="Без ограничения"></div><div class="filter-actions"><button class="btn" type="submit">Показать</button><button class="btn ghost" type="button" id="clear-filters">Сбросить</button></div></form></aside>
  <div class="results">${items.length ? items.map(listingCard).join('') : empty('Пока нет предложений','Попробуйте изменить фильтры или загляните позже.')}</div></div>`;
}
function listingCard(x, mine=false) {
  return `<article class="card"><div><span class="tag ${x.status==='available'?'dark':''}">${statuses[x.status]}</span><h3>${escapeHtml(x.title)}</h3><div class="location">⌖ ${escapeHtml(x.city)} · ${escapeHtml(x.address)}</div><p>${escapeHtml(x.description||'Площадка для размещения вагонов. Подробности уточняйте у владельца.')}</p><div class="tags"><span class="tag">${x.free_capacity} из ${x.capacity} мест свободно</span><span class="tag">${escapeHtml(x.owner_name)}</span></div></div><div class="price"><strong>${money(x.price)}</strong><small>за вагон</small></div><div class="card-bottom"><span class="hint">${mine?'Ваше объявление':'Заявка будет отправлена владельцу пути на подтверждение'}</span>${mine ? `<div class="deal-actions"><button class="btn small" type="button" data-edit="${x.id}">Редактировать объявление</button>${x.status==='available'?`<button class="btn secondary small" data-withdraw="${x.id}">Снять объявление</button>`:''}</div>` : state.user?.role==='wagon'?`<button class="btn small" data-request="${x.id}" data-free="${x.free_capacity}">Отправить заявку</button>` : !state.user?'<a class="btn small" href="#login">Войти и откликнуться</a>':''}</div></article>`;
}
function renderAuth(page) {
  const signup=page==='register';
  app.innerHTML=`<div class="auth-wrap"><div class="form-card"><div class="eyebrow">ВагонМесто</div><h1>${signup?'Создать аккаунт':'С возвращением'}</h1><p>${signup?'Выберите роль, чтобы открыть нужные инструменты.':'Войдите, чтобы работать с объявлениями и заявками.'}</p><form id="auth-form">${signup?`<div class="field"><label>Ваше имя</label><input name="name" required minlength="2" maxlength="100" autocomplete="name"></div>`:''}<div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div><div class="field"><label>Пароль</label><input name="password" type="password" required minlength="${signup?8:1}" autocomplete="${signup?'new-password':'current-password'}"></div>${signup?`<div class="field"><label>Тип аккаунта</label><select name="role"><option value="wagon">Владелец вагонов</option><option value="track">Владелец путей</option></select></div>`:''}<button class="btn" type="submit">${signup?'Зарегистрироваться':'Войти'}</button></form><div class="auth-switch">${signup?'Уже есть аккаунт? <a href="#login">Войти</a>':'Нет аккаунта? <a href="#register">Зарегистрироваться</a>'}</div></div></div>`;
}
async function renderAccount() {
  const user=state.user;
  const [deals, additional]=await Promise.all([api('/my/deals'),api(user.role==='track'?'/my/listings':'/company')]);
  const list=user.role==='track'?additional:null, company=user.role==='wagon'?additional:null;
  app.innerHTML=`${pageHeading('Личный кабинет','Управляйте площадками и следите за заявками в одном месте.')}<div class="account-banner"><div><div class="eyebrow">${roles[user.role]}</div><h2>${escapeHtml(user.name)}</h2><p>${escapeHtml(user.email)}</p></div><a class="btn secondary" href="#${user.role==='track'?'create':'company'}">${user.role==='track'?'+ Добавить площадку':company?'Моя компания':'+ Создать компанию'}</a></div>
  <div class="columns"><div class="metric"><h3>${user.role==='track'?'Мои объявления':'Моя компания'}</h3>${user.role==='track'?`<strong>${list.length}</strong><p>Опубликованных и снятых объявлений</p>`:company?`<strong>${escapeHtml(company.name)}</strong><p>ИНН ${escapeHtml(company.inn)}</p>`:`<p>Создайте компанию, чтобы отправлять заявки на места.</p><a class="btn small" href="#company">Создать</a>`}</div><div class="metric"><h3>Сделки и заявки</h3><strong>${deals.filter(d=>d.status==='confirmed').length}</strong><p>Подтвержденных сделок · ${deals.filter(d=>d.status==='pending').length} ожидают решения</p></div></div>
  <h3 class="subhead">${user.role==='track'?'Входящие заявки':'История сделок и заявок'}</h3><div class="list-stack">${deals.length?deals.map(dealCard).join(''):empty('Заявок пока нет','Когда появится первая заявка, она будет здесь.')}</div>
  ${list?`<h3 class="subhead">Мои площадки</h3><div class="results">${list.length?list.map(x=>listingCard(x,true)).join(''):empty('Объявлений пока нет','Добавьте площадку, чтобы она появилась в общем списке.','<a class="btn" href="#create">Создать объявление</a>')}</div>`:''}`;
}
function dealCard(d) {
  return `<article class="deal"><div class="deal-head"><div><h4>${escapeHtml(d.title)}</h4><p>${escapeHtml(d.city)} · ${d.wagons} ваг. · ${money(d.price)} за вагон · ${escapeHtml(d.company_name)}</p></div><span class="badge ${d.status}">${statuses[d.status]}</span></div><div class="hint">Заявка №${d.id} · ${escapeHtml(d.created_at)}</div>${state.user.role==='track'?`<div class="applicant"><strong>Отправитель: ${escapeHtml(d.wagon_name)}</strong><span>Личный email: ${escapeHtml(d.wagon_email)}</span><span>Компания: ${escapeHtml(d.company_name)} · ИНН ${escapeHtml(d.company_inn)}</span><span>Email компании: ${escapeHtml(d.company_email)}${d.company_phone?` · Телефон: ${escapeHtml(d.company_phone)}`:''}</span></div>`:''}${d.status==='pending'&&state.user.role==='track'?`<div class="deal-actions"><button class="btn small" data-decision="${d.id}" data-status="confirmed">Подтвердить</button><button class="btn secondary small" data-decision="${d.id}" data-status="rejected">Отклонить</button></div>`:''}</article>`;
}
function renderCreate() {
  renderListingForm();
}
async function renderEdit(id) {
  const items=await api('/my/listings');
  const item=items.find(x=>x.id===id);
  if (!item) { app.innerHTML=empty('Объявление не найдено','Проверьте список своих площадок.','<a class="btn" href="#account">В кабинет</a>'); return; }
  renderListingForm(item);
}
function renderListingForm(item=null) {
  const edit=Boolean(item), value=key=>escapeHtml(item?.[key]??'');
  app.innerHTML=`${pageHeading(edit?'Изменить объявление':'Новое объявление',edit?'Обновите сведения о площадке. Изменение цены не меняет стоимость уже созданных заявок.':'Опишите площадку, чтобы владельцы вагонов могли оценить условия.')}
  <div class="form-card"><form id="listing-form" ${edit?`data-listing-id="${item.id}"`:''} class="form-grid"><div class="field full"><label>Название площадки</label><input name="title" required minlength="3" maxlength="120" placeholder="Например, Путь у грузовой станции" value="${value('title')}"></div><div class="field"><label>Город</label><input name="city" required minlength="2" maxlength="100" placeholder="Москва" value="${value('city')}"></div><div class="field"><label>Адрес / ориентир</label><input name="address" required minlength="3" maxlength="200" placeholder="ул. Промышленная, 12" value="${value('address')}"></div><div class="field"><label>Вместимость, вагонов</label><input name="capacity" type="number" min="${Math.max(1,edit?item.capacity-item.free_capacity:1)}" max="100000" required placeholder="20" value="${value('capacity')}"></div><div class="field"><label>Цена за вагон, ₽</label><input name="price" type="number" min="0.01" step="0.01" required placeholder="1500" value="${value('price')}"></div><div class="field full"><label>Описание условий</label><textarea name="description" maxlength="3000" placeholder="Длина пути, доступ, услуги и другие детали">${value('description')}</textarea><span class="hint">${edit?'Подтверждённые сделки сохраняют занятые места. Снятое объявление останется снятым.':'Дополнительные характеристики можно будет добавить позже.'}</span></div><div class="form-actions full"><button class="btn" type="submit">${edit?'Сохранить изменения':'Опубликовать объявление'}</button><a class="btn ghost" href="#account">Отмена</a></div></form></div>`;
}
async function renderCompany() {
  const company=await api('/company');
  app.innerHTML=pageHeading('Компания','Данные компании используются в заявках на вагоно-места.')+(company?`<div class="form-card"><span class="tag dark">Компания создана</span><h2>${escapeHtml(company.name)}</h2><p>ИНН: ${escapeHtml(company.inn)}</p><p>Email: ${escapeHtml(company.email)}</p><p>Телефон: ${escapeHtml(company.phone||'не указан')}</p><a class="btn secondary" href="#account">В кабинет</a></div>`:`<div class="form-card"><form id="company-form" class="form-grid"><div class="field full"><label>Название компании</label><input name="name" required minlength="2" maxlength="200" placeholder="ООО «Транспорт»"></div><div class="field"><label>ИНН (10 или 12 цифр)</label><input name="inn" required pattern="[0-9]{10}([0-9]{2})?" inputmode="numeric" placeholder="1234567890"></div><div class="field"><label>Рабочий email</label><input name="email" type="email" required placeholder="office@example.ru"></div><div class="field full"><label>Телефон (необязательно)</label><input name="phone" type="tel" maxlength="30" placeholder="+7 900 000-00-00"></div><div class="form-actions full"><button class="btn" type="submit">Сохранить компанию</button></div></form></div>`);
}
async function handleSubmit(e) {
  const form=e.target; if (!['auth-form','filters','listing-form','company-form'].includes(form.id)) return;
  e.preventDefault(); const v=values(form); const button=form.querySelector('[type=submit]'); button.disabled=true;
  try {
    if (form.id==='filters') { state.filters=Object.fromEntries(Object.entries(v).filter(([,value])=>value)); await render(); }
    if (form.id==='auth-form') { const result=await send(location.hash==='#register'?'/register':'/login',v); state.token=result.token; state.user=result.user; localStorage.setItem('wagonhub_token',result.token); location.hash='account'; toast('Вы вошли в аккаунт'); }
    if (form.id==='listing-form') {
      const payload={...v,capacity:Number(v.capacity)};
      if (form.dataset.listingId) {
        await api(`/listings/${form.dataset.listingId}`,{method:'PUT',body:JSON.stringify(payload)});
        toast('Объявление обновлено');
      } else { await send('/listings',payload); toast('Объявление опубликовано'); }
      location.hash='account';
    }
    if (form.id==='company-form') { await send('/company',v); toast('Компания создана'); location.hash='account'; }
  } catch(error) { toast(error.message,true); } finally { button.disabled=false; }
}
async function handleClick(e) {
  const target=e.target.closest('button'); if (!target) return;
  try {
    if (target.id==='retry') await render();
    if (target.id==='clear-filters') { state.filters={}; await render(); }
    if (target.dataset.edit) location.hash=`edit/${target.dataset.edit}`;
    if (target.id==='logout') { await api('/logout',{method:'POST'}); state.token=null; state.user=null; localStorage.removeItem('wagonhub_token'); location.hash='listings'; await render(); }
    if (target.dataset.withdraw) { if (!confirm('Снять объявление? Ожидающие заявки будут отклонены.')) return; await send(`/listings/${target.dataset.withdraw}/withdraw`,{}); toast('Объявление снято'); await render(); }
    if (target.dataset.request) {
      const wagons=prompt(`Сколько вагонов разместить? Свободно: ${target.dataset.free}`,'1');
      if (wagons===null) return;
      const count=Number(wagons); if (!Number.isInteger(count)||count<1) throw new Error('Укажите целое число вагонов больше нуля');
      await send('/deals',{listing_id:Number(target.dataset.request),wagons:count}); toast('Заявка отправлена'); location.hash='account';
    }
    if (target.dataset.decision) { await send(`/deals/${target.dataset.decision}/decision`,{status:target.dataset.status}); toast('Решение сохранено'); await render(); }
  } catch(error) { toast(error.message,true); }
}
document.addEventListener('submit',handleSubmit);
document.addEventListener('click',handleClick);
window.addEventListener('hashchange',render);
(async()=>{ if (state.token) { try { state.user=await api('/me'); } catch { state.token=null; localStorage.removeItem('wagonhub_token'); } } render(); })();
