// Invitation fragments are removed before rendering. Their values live only in memory.
let linkInvite = null;
if (location.pathname === '/invite') {
  const link = /^#([A-Za-z0-9_-]{43})\/(track|wagon)$/.exec(location.hash);
  if (link) linkInvite = { token: link[1], role: link[2] };
  history.replaceState(null, '', '/#register' + (link ? '?role=' + link[2] : '?invalid_invite=1'));
}

const app = document.querySelector('#app');
let recovery = {step:'email', email:'', token:null};
localStorage.removeItem('wagonhub_token');
const state = { user: null, filters: {}, before: null, next: null, cursors: [], alerts: [], newInvite: null };
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money = value => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value);
const roles = {track:'Владелец путей',wagon:'Владелец вагонов'};
const statuses = {available:'Доступно',withdrawn:'Снято',pending:'Ожидает решения',confirmed:'Подтверждено',rejected:'Отклонено',completed:'Завершено'};
const toast = (message, error=false) => { const node=document.querySelector('#toast'); node.textContent=message; node.className=(error?'error ':'')+'show'; clearTimeout(toast.timer); toast.timer=setTimeout(()=>node.className='',4000); };
async function api(path, options={}) {
  const headers = {...options.headers};
  if (options.method && options.method !== 'GET') headers['X-WagonHub-Request']='1';
  if (options.body) headers['Content-Type']='application/json';
  let response;
  try { response = await fetch('/api'+path,{...options,headers}); }
  catch { throw new Error('Нет соединения с сервером'); }
  if (response.status===204) return null;
  const data=await response.json().catch(()=>null);
  if (!response.ok) {
    if (response.status===401 && path!=='/login') { state.user=null; state.newInvite=null; }
    const detail = data?.detail;
    throw new Error(typeof detail==='string' ? detail : Array.isArray(detail) ? detail.map(x=>x.msg).join('; ') : 'Не удалось выполнить действие');
  }
  return data;
}
const send = (path, body) => api(path,{method:'POST',body:JSON.stringify(body)});
const values = form => Object.fromEntries(new FormData(form).entries());
function pageHeading(title, description) { return `<div class="page-heading"><h1>${title}</h1><p>${description}</p></div>`; }
function empty(title, description, action='') { return `<div class="empty"><div class="symbol">◇</div><h3>${title}</h3><p>${description}</p>${action}</div>`; }
function currentPage() { return location.hash.slice(1).split('?')[0] || (location.pathname==='/admin'?'admin':location.pathname==='/reset-password'?'reset-password':'home'); }
function refreshNav() {
  const page=currentPage();
  document.querySelector('#admin-link').hidden=!state.user?.is_admin;
  document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===page));
  document.querySelector('#create-link').hidden=state.user?.role!=='track';
  document.querySelector('#auth-actions').innerHTML=state.user ? `<button class="btn ghost small" id="logout">Выйти</button>` : `<a class="btn small" href="#login" data-goal="open_login">Войти</a>`;
}
async function render() {
  refreshNav();
  const page=currentPage();
  try {
    if (page==='home') return renderHome();
    if (page==='listings') return await renderListings();
    if (page==='reset-password') return renderRecovery();
    if (page==='login'||page==='register') return renderAuth(page);
    if (!state.user) { location.hash='login'; return; }
    if (page==='admin') {
      if (!state.user.is_admin) return emptyInApp('Нет доступа', 'Раздел доступен только мастер-аккаунту.');
      if (location.pathname!=='/admin') { location.assign('/admin'); return; }
      return await renderAdmin();
    }
    if (page==='account') return await renderAccount();
    if (page==='create'&&state.user.role==='track') return renderCreate();
    if (/^edit\/\d+$/.test(page)&&state.user.role==='track') return await renderEdit(Number(page.split('/')[1]));
    if (page==='company'&&state.user.role==='wagon') return await renderCompany();
    location.hash='home';
  } catch(error) { app.innerHTML=empty('Не удалось загрузить страницу',escapeHtml(error.message),'<button class="btn" id="retry">Повторить</button>'); }
}
function emptyInApp(title, description) { app.innerHTML=empty(title,description,'<a class="btn" href="/#account">В личный кабинет</a>'); }
function renderHome() {
  app.innerHTML=`<section class="landing-hero"><div><span class="eyebrow">Площадка для железнодорожной логистики</span><h1>Место для вагонов.<br>Заявка без цепочки звонков.</h1><p>Владельцы путей размещают свободные места и условия. Операторы сравнивают предложения и отправляют заявку прямо на сайте.</p><div class="landing-actions"><a class="btn" href="#listings" data-goal="landing_find_places">Найти место отстоя</a><a class="btn secondary" href="#register?role=track" data-goal="landing_register_track">Разместить площадку</a></div></div><div class="landing-aside"><span>01 / Предложение</span><strong>Площадка и цена</strong><span>02 / Поиск</span><strong>Город и вместимость</strong><span>03 / Заявка</span><strong>Ответ владельца пути</strong></div></section>
  <section class="landing-section"><div class="section-heading"><div><h2>Как работает сервис</h2><p>Две роли и понятный порядок действий</p></div></div><div class="landing-steps"><article><span>01</span><h3>Публикация</h3><p>Владелец пути указывает адрес, вместимость, цену и условия размещения.</p></article><article><span>02</span><h3>Выбор</h3><p>Оператор ищет площадку по городу, свободным местам и цене.</p></article><article><span>03</span><h3>Решение</h3><p>Владелец пути видит данные заявителя и подтверждает или отклоняет запрос.</p></article></div></section>
  <section class="landing-end"><div><h2>Начните с доступных предложений</h2><p>Посмотрите площадки или создайте аккаунт для размещения объявления.</p></div><div class="landing-actions"><a class="btn" href="#listings" data-goal="landing_find_places">Открыть каталог</a><a class="btn secondary" href="#register" data-goal="landing_register_wagon">Создать аккаунт</a></div></section>`;
}
async function renderListings() {
  const qs=new URLSearchParams(Object.entries(state.filters).filter(([,v])=>v));
  qs.set('limit','51');
  if (state.before) qs.set('before', String(state.before));
  const page=await api('/listings?'+qs);
  const items=page.slice(0,50);
  state.next=page.length>50 ? items.at(-1).id : null;
  const city=escapeHtml(state.filters.city||'');
  app.innerHTML=`<section class="hero"><div class="hero-content"><div class="eyebrow">Инфраструктура рядом</div><h1>Место для ваших вагонов начинается здесь.</h1><p>Находите свободные пути, сравнивайте условия и отправляйте заявку владельцу площадки.</p><div class="hero-stat"><div><strong>${items.length}</strong> предложений на странице</div><div><strong>24/7</strong> доступ к площадке</div></div></div></section>
  <div class="section-heading"><div><h2>Актуальные предложения</h2><p>Свободные вагоно-места от владельцев путей</p></div></div>
  <div class="layout"><aside class="panel"><h3>Найти площадку</h3><form id="filters"><div class="field"><label for="city">Город</label><input id="city" name="city" placeholder="Например, Москва" value="${city}"></div><div class="field"><label for="capacity">Количество вагонов от</label><input id="capacity" name="min_capacity" type="number" min="1" value="${escapeHtml(state.filters.min_capacity||'') }" placeholder="Любое"></div><div class="field"><label for="max_price">Цена за вагон до, ₽</label><input id="max_price" name="max_price" type="number" min="1" step="0.01" value="${escapeHtml(state.filters.max_price||'')}" placeholder="Без ограничения"></div><div class="filter-actions"><button class="btn" type="submit">Показать</button><button class="btn ghost" type="button" id="clear-filters" data-goal="clear_filters">Сбросить</button></div></form></aside>
  <div class="results">${items.length ? items.map(listingCard).join('') : empty('Пока нет предложений','Попробуйте изменить фильтры или загляните позже.')}</div></div>
  <div class="deal-actions">${state.cursors.length?'<button class="btn secondary" id="catalog-prev">Назад</button>':''}${state.next?'<button class="btn" id="catalog-next">Далее</button>':''}</div>`;
}
function listingCard(x, mine=false) {
  return `<article class="card"><div><span class="tag ${x.status==='available'?'dark':''}">${statuses[x.status]}</span><h3>${escapeHtml(x.title)}</h3><div class="location">⌖ ${escapeHtml(x.city)} · ${escapeHtml(x.address)}</div><p>${escapeHtml(x.description||'Площадка для размещения вагонов. Подробности уточняйте у владельца.')}</p><div class="tags"><span class="tag">${x.free_capacity} из ${x.capacity} мест свободно</span><span class="tag">${escapeHtml(x.owner_name)}</span></div></div><div class="price"><strong>${money(x.price)}</strong><small>за вагон</small></div><div class="card-bottom"><span class="hint">${mine?'Ваше объявление':'Заявка будет отправлена владельцу пути на подтверждение'}</span>${mine ? `<div class="deal-actions"><button class="btn small" type="button" data-edit="${x.id}" data-goal="edit_listing_click">Редактировать объявление</button>${x.status==='available'?`<button class="btn secondary small" data-withdraw="${x.id}">Снять объявление</button>`:''}</div>` : state.user?.role==='wagon'?`<button class="btn small" data-request="${x.id}" data-free="${x.free_capacity}" data-goal="request_click">Отправить заявку</button>` : !state.user?'<a class="btn small" href="#login" data-goal="request_login_click">Войти и откликнуться</a>':''}</div></article>`;
}
function renderAuth(page) {
  const signup=page==='register';
  const params=new URLSearchParams(location.hash.split('?')[1]||'');
  const trackSignup=(linkInvite?.role||params.get('role'))==='track';
  app.innerHTML=`<div class="auth-wrap"><div class="form-card"><div class="eyebrow">ВагонМесто</div><h1>${signup?'Создать аккаунт':'С возвращением'}</h1><p>${signup?'Выберите роль. Для участия в пилоте используйте данные из приглашения.':'Войдите, чтобы работать с объявлениями и заявками.'}</p>${signup&&linkInvite?'<p class="hint">Ссылка прикреплена. Используйте email, на который вас пригласили.</p>':''}${signup&&params.has('invalid_invite')?'<p class="hint">Ссылка неполная. Попросите администратора прислать её ещё раз.</p>':''}<form id="auth-form">${signup?`<div class="field"><label>Ваше имя</label><input name="name" required minlength="2" maxlength="100" autocomplete="name"></div>`:''}<div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div><div class="field"><label>Пароль</label><input name="password" type="password" required minlength="${signup?8:1}" autocomplete="${signup?'new-password':'current-password'}"></div>${signup?`<div class="field"><label>Тип аккаунта</label><select name="role"${linkInvite?' disabled':''}><option value="wagon" ${trackSignup?'':'selected'}>Владелец вагонов</option><option value="track" ${trackSignup?'selected':''}>Владелец путей</option></select></div>${(window.WAGONHUB_INVITE_REQUIRED||window.WAGONHUB_ADMIN_ENABLED)&&!linkInvite?`<div class="field"><label>Код приглашения</label><input name="invite_code" ${window.WAGONHUB_INVITE_REQUIRED?'required':''} maxlength="128" autocomplete="off"></div>`:''}`:''}<button class="btn" type="submit">${signup?'Зарегистрироваться':'Войти'}</button></form>${!signup&&window.WAGONHUB_PASSWORD_RESET_ENABLED?'<div class="auth-switch"><a href="/reset-password">Забыли пароль?</a></div>':''}<div class="auth-switch">${signup?'Уже есть аккаунт? <a href="#login">Войти</a>':'Нет аккаунта? <a href="#register">Зарегистрироваться</a>'}</div></div></div>`;
}
async function renderAccount() {
  const user=state.user;
  const [deals, additional, alerts]=await Promise.all([api('/my/deals'),api(user.role==='track'?'/my/listings':'/company'),api('/my/notifications')]);
  state.alerts=alerts;
  const list=user.role==='track'?additional:null, company=user.role==='wagon'?additional:null;
  app.innerHTML=`${pageHeading('Личный кабинет','Управляйте площадками и следите за заявками в одном месте.')}<div class="account-banner"><div><div class="eyebrow">${roles[user.role]}</div><h2>${escapeHtml(user.name)}</h2><p>${escapeHtml(user.email)}</p></div><a class="btn secondary" href="#${user.role==='track'?'create':'company'}">${user.role==='track'?'+ Добавить площадку':company?'Моя компания':'+ Создать компанию'}</a></div>
  <div class="columns"><div class="metric"><h3>${user.role==='track'?'Мои объявления':'Моя компания'}</h3>${user.role==='track'?`<strong>${list.length}</strong><p>Опубликованных и снятых объявлений</p>`:company?`<strong>${escapeHtml(company.name)}</strong><p>ИНН ${escapeHtml(company.inn)}</p>`:`<p>Создайте компанию, чтобы отправлять заявки на места.</p><a class="btn small" href="#company">Создать</a>`}</div><div class="metric"><h3>Сделки и заявки</h3><strong>${deals.filter(d=>d.status==='confirmed').length}</strong><p>Подтвержденных сделок · ${deals.filter(d=>d.status==='pending').length} ожидают решения</p></div></div>
  ${alerts.length?`<div class="panel"><h3>Новые уведомления</h3><ul>${alerts.map(a=>`<li>Заявка №${a.deal_id}: ${statuses[a.event]}</li>`).join('')}</ul><button class="btn secondary small" id="alerts-seen">Отметить прочитанными</button></div>`:''}
  <h3 class="subhead">${user.role==='track'?'Входящие заявки':'История сделок и заявок'}</h3><div class="list-stack">${deals.length?deals.map(dealCard).join(''):empty('Заявок пока нет','Когда появится первая заявка, она будет здесь.')}</div>
  ${list?`<h3 class="subhead">Мои площадки</h3><div class="results">${list.length?list.map(x=>listingCard(x,true)).join(''):empty('Объявлений пока нет','Добавьте площадку, чтобы она появилась в общем списке.','<a class="btn" href="#create">Создать объявление</a>')}</div>`:''}`;
}
function dealCard(d) {
  return `<article class="deal"><div class="deal-head"><div><h4>${escapeHtml(d.title)}</h4><p>${escapeHtml(d.city)} · ${d.wagons} ваг. · ${money(d.price)} за вагон · ${escapeHtml(d.company_name)}</p></div><span class="badge ${d.status}">${statuses[d.status]}</span></div><div class="hint">Заявка №${d.id} · ${escapeHtml(d.created_at)}</div>${state.user.role==='track'?`<div class="applicant"><strong>Отправитель: ${escapeHtml(d.wagon_name)}</strong><span>Личный email: ${escapeHtml(d.wagon_email)}</span><span>Компания: ${escapeHtml(d.company_name)} · ИНН ${escapeHtml(d.company_inn)}</span><span>Email компании: ${escapeHtml(d.company_email)}${d.company_phone?` · Телефон: ${escapeHtml(d.company_phone)}`:''}</span></div>`:''}${d.track_email&&state.user.role==='wagon'?`<div class="applicant">Email владельца пути: ${escapeHtml(d.track_email)}</div>`:''}${d.status==='confirmed'&&state.user.role==='track'?`<button class="btn secondary small" data-complete="${d.id}">Завершить и освободить места</button>`:''}${d.status==='pending'&&state.user.role==='track'?`<div class="deal-actions"><button class="btn small" data-decision="${d.id}" data-status="confirmed">Подтвердить</button><button class="btn secondary small" data-decision="${d.id}" data-status="rejected">Отклонить</button></div>`:''}</article>`;
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
async function renderAdmin() {
  const items=await api('/admin/invites');
  const labels={pending:'Действует',used:'Использовано',expired:'Истекло',revoked:'Отозвано'};
  const expiry=seconds=>new Date(seconds*1000).toLocaleString('ru-RU');
  const created=state.newInvite && items.some(item=>item.id===state.newInvite.id && item.status==='pending');
  app.innerHTML=`<section class="page-heading"><h1>Приглашения</h1><p>Ссылки действуют 7 дней и используются для регистрации один раз.</p></section>
    <div class="form-card"><form id="invite-form" class="form-grid">
      <div class="field"><label for="invite-email">Email участника</label><input id="invite-email" name="email" type="email" required></div>
      <div class="field"><label for="invite-role">Роль</label><select id="invite-role" name="role"><option value="track">Владелец путей</option><option value="wagon">Владелец вагонов</option></select></div>
      <div class="field full" id="invite-inn-field" hidden><label for="invite-inn">ИНН компании</label><input id="invite-inn" name="inn" inputmode="numeric" pattern="[0-9]{10}([0-9]{2})?" disabled></div>
      <button class="btn" type="submit">Создать приглашение</button>
    </form></div>
    ${created?`<div class="panel invite-result"><label for="invite-link">Ссылка для отправки</label><p class="hint">Скопируйте ссылку сейчас: после обновления страницы она не показывается.</p><div class="field"><input id="invite-link" readonly value="${escapeHtml(state.newInvite.url)}"></div><button class="btn" type="button" id="copy-invite">Скопировать ссылку</button></div>`:''}
    <h2 class="subhead">Последние приглашения</h2><div class="list-stack">
      ${items.map(item=>`<article class="deal"><div class="deal-head"><div><strong>${escapeHtml(item.email)}</strong><p>${roles[item.role]}${item.inn?` · ИНН ${escapeHtml(item.inn)}`:''} · Срок до ${escapeHtml(expiry(item.expires_at))}</p></div><span class="tag">${labels[item.status]}</span></div>
      ${item.status==='pending'?`<button class="btn secondary small" type="button" data-revoke-invite="${escapeHtml(item.id)}">Отозвать</button>`:''}</article>`).join('')||empty('Приглашений пока нет','Создайте первое приглашение в форме выше.')}</div>`;
  document.querySelector('#invite-role').addEventListener('change', event=>{
    const needed=event.target.value==='wagon';
    const input=document.querySelector('#invite-inn');
    document.querySelector('#invite-inn-field').hidden=!needed;
    input.disabled=!needed;
    input.required=needed;
  });
}

function renderRecovery() {
  if (!window.WAGONHUB_PASSWORD_RESET_ENABLED) {
    app.innerHTML=empty('Восстановление недоступно', 'Отправка писем пока не настроена.', '<a class="btn" href="/#login">Ко входу</a>');
    return;
  }
  const step=recovery.step;
  const form=step==='email'
    ? `<div class="field"><label for="reset-email">Email аккаунта</label><input id="reset-email" name="email" type="email" required autocomplete="email" value="${escapeHtml(recovery.email)}"></div><button class="btn" type="submit">Получить код</button>`
    : step==='code'
      ? '<div class="field"><label for="reset-code">Код из письма</label><input id="reset-code" name="code" required inputmode="numeric" pattern="[0-9]{8}" maxlength="8" autocomplete="one-time-code"></div><button class="btn" type="submit">Подтвердить код</button><button class="btn ghost" type="button" id="reset-resend">Запросить новый код</button>'
      : '<div class="field"><label for="reset-new">Новый пароль</label><input id="reset-new" name="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password"></div><div class="field"><label for="reset-confirm">Повторите новый пароль</label><input id="reset-confirm" name="confirm" type="password" minlength="8" maxlength="128" required autocomplete="new-password"></div><button class="btn" type="submit">Сохранить пароль</button>';
  const description=step==='email'?'Введите email, который использовали при регистрации.'
    :step==='code'?`Если аккаунт доступен, код отправлен на ${escapeHtml(recovery.email)}. Проверьте папку «Спам». Между отправками должна пройти минута.`
      :'Код подтверждён. Придумайте новый пароль.';
  app.innerHTML=`<div class="auth-wrap"><div class="form-card"><h1>Восстановление пароля</h1><p>${description}</p>
    <form id="reset-form">${form}<p id="reset-error" class="hint" role="alert"></p></form>
    <div class="auth-switch"><a href="/#login">Вернуться ко входу</a>${step!=='email'?' · <button class="btn ghost small" type="button" id="reset-start">Начать заново</button>':''}</div></div></div>`;
}

async function submitRecovery(form) {
  const fields=values(form);
  if (recovery.step==='email') {
    const email=fields.email.trim();
    await send('/password-reset/request',{email});
    recovery={step:'code',email,token:null};
  } else if (recovery.step==='code') {
    const result=await send('/password-reset/verify',{email:recovery.email,code:fields.code});
    recovery={...recovery,step:'password',token:result.token};
  } else {
    if (fields.password!==fields.confirm) throw new Error('Пароли не совпадают');
    await send('/password-reset/complete',{email:recovery.email,token:recovery.token,password:fields.password});
    recovery={step:'email',email:'',token:null};
    state.user=null;
    state.newInvite=null;
    history.replaceState(null,'','/#login');
    await render();
    toast('Пароль изменён. Войдите с новым паролем.');
    return;
  }
  renderRecovery();
}

async function handleSubmit(e) {
  const form=e.target; if (!['auth-form','filters','listing-form','company-form','invite-form','reset-form'].includes(form.id)) return;
  e.preventDefault(); const v=values(form); const button=form.querySelector('[type=submit]'); button.disabled=true;
  try {
    if (form.id==='reset-form') { await submitRecovery(form); return; }
    if (form.id==='filters') { state.before=null; state.cursors=[]; state.filters=Object.fromEntries(Object.entries(v).filter(([,value])=>value)); window.wagonhubAnalytics?.goal('filters_submit'); await render(); }
    if (form.id==='auth-form') { const signup=location.hash.startsWith('#register'); if (signup && linkInvite) { v.invite_token=linkInvite.token; v.role=linkInvite.role; } const result=await send(signup?'/register':'/login',v); linkInvite=null; state.user=result.user; window.wagonhubAnalytics?.goal(signup?'registration_success':'login_success'); if (location.pathname==='/admin'&&state.user.is_admin) { history.replaceState(null,'','/admin'); await render(); } else { location.hash='account'; } toast('Вы вошли в аккаунт'); }
    if (form.id==='listing-form') {
      const payload={...v,capacity:Number(v.capacity)};
      if (form.dataset.listingId) {
        await api(`/listings/${form.dataset.listingId}`,{method:'PUT',body:JSON.stringify(payload)});
        window.wagonhubAnalytics?.goal('listing_updated'); toast('Объявление обновлено');
      } else { await send('/listings',payload); window.wagonhubAnalytics?.goal('listing_created'); toast('Объявление опубликовано'); }
      location.hash='account';
    }
    if (form.id==='invite-form') {
      const result=await send('/admin/invites',{email:v.email,role:v.role,inn:v.role==='wagon'?v.inn:null});
      const url=new URL('/invite',location.origin);
      url.hash=result.token+'/'+result.role;
      state.newInvite={id:result.id,url:url.href};
      await render();
      toast('Приглашение создано');
    }
    if (form.id==='company-form') { await send('/company',v); window.wagonhubAnalytics?.goal('company_created'); toast('Компания создана'); location.hash='account'; }
  } catch(error) {
    if (form.id==='reset-form') { const errorNode=form.querySelector('#reset-error'); if (errorNode) errorNode.textContent=error.message; }
    else toast(error.message,true);
  } finally { button.disabled=false; }
}
async function handleClick(e) {
  const target=e.target.closest('button'); if (!target) return;
  try {
    if (target.id==='retry') await render();
    if (target.id==='reset-start') {
      recovery={step:'email',email:recovery.email,token:null};
      renderRecovery();
    }
    if (target.id==='reset-resend' && recovery.step==='code') {
      target.disabled=true;
      const feedback=document.querySelector('#reset-error');
      try {
        await send('/password-reset/request',{email:recovery.email});
        feedback.textContent='Если прошло не менее минуты, новый код отправлен.';
      } catch(error) { feedback.textContent=error.message; }
      finally { target.disabled=false; }
    }
    if (target.id==='clear-filters') { state.filters={}; state.before=null; state.cursors=[]; await render(); }
    if (target.id==='catalog-next' && state.next) { state.cursors.push(state.before); state.before=state.next; await render(); }
    if (target.id==='catalog-prev' && state.cursors.length) { state.before=state.cursors.pop(); await render(); }
    if (target.id==='copy-invite') {
      const input=document.querySelector('#invite-link');
      try { await navigator.clipboard.writeText(input.value); toast('Ссылка скопирована'); }
      catch { input.focus(); input.select(); toast('Ссылка выделена. Скопируйте её вручную'); }
    }
    if (target.dataset.revokeInvite) {
      if (!confirm('Отозвать приглашение? По этой ссылке больше нельзя будет зарегистрироваться.')) return;
      await send('/admin/invites/'+target.dataset.revokeInvite+'/revoke',{});
      if (state.newInvite?.id===target.dataset.revokeInvite) state.newInvite=null;
      await render();
    }
    if (target.id==='alerts-seen') { await send('/my/notifications/seen',state.alerts); await render(); }
    if (target.dataset.complete) {
      if (!confirm('Вагоны уже покинули площадку? Места снова появятся в каталоге.')) return;
      target.disabled=true;
      try { await send(`/deals/${target.dataset.complete}/complete`,{}); await render(); }
      finally { target.disabled=false; }
    }
    if (target.dataset.edit) location.hash=`edit/${target.dataset.edit}`;
    if (target.id==='logout') { await api('/logout',{method:'POST'}); state.user=null; state.newInvite=null; location.hash='listings'; await render(); }
    if (target.dataset.withdraw) { if (!confirm('Снять объявление? Ожидающие заявки будут отклонены.')) return; await send(`/listings/${target.dataset.withdraw}/withdraw`,{}); window.wagonhubAnalytics?.goal('listing_withdrawn'); toast('Объявление снято'); await render(); }
    if (target.dataset.request) {
      const wagons=prompt(`Сколько вагонов разместить? Свободно: ${target.dataset.free}`,'1');
      if (wagons===null) return;
      const count=Number(wagons); if (!Number.isInteger(count)||count<1) throw new Error('Укажите целое число вагонов больше нуля');
      target.disabled=true;
      try { await send('/deals',{listing_id:Number(target.dataset.request),wagons:count}); window.wagonhubAnalytics?.goal('request_sent'); toast('Заявка отправлена'); location.hash='account'; } finally { target.disabled=false; }
    }
    if (target.dataset.decision) { await send(`/deals/${target.dataset.decision}/decision`,{status:target.dataset.status}); window.wagonhubAnalytics?.goal(target.dataset.status==='confirmed'?'request_confirmed':'request_rejected'); toast('Решение сохранено'); await render(); }
  } catch(error) { toast(error.message,true); }
}
document.addEventListener('submit',handleSubmit);
document.addEventListener('click',handleClick);
window.addEventListener('hashchange',render);
(async()=>{ try { state.user=await api('/me'); } catch {} render(); })();
