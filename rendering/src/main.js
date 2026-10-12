import './style.css';
import { AtlasWorld, LANGUAGE_COLORS, DISTRICT_COLORS } from './world.js';
import { createGuideIndex, toursForFile } from './guides.js';
import { createScenarioIndex, examplePayload } from './scenarios.js';
import { createDebugger } from './debugger.js';

const ICONS = {
  cube: '<path d="m12 3 9 5v9l-9 5-9-5V8l9-5Z"/><path d="m3 8 9 5 9-5M12 13v9"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  orbit: '<circle cx="12" cy="12" r="3"/><ellipse cx="12" cy="12" rx="10" ry="6" transform="rotate(-30 12 12)"/>',
  walk: '<circle cx="14" cy="4" r="2"/><path d="m10 21 2-6-3-3 3-5 3 4 4 1M7 10l3-3m3 8 4 6"/>',
  reset: '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  previous: '<path d="m15 5-7 7 7 7"/>',
  tour: '<path d="m8 4 13 8-13 8V4Z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 10 9 5 9-5M3 18l9 5 9-5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.5"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5 5-3Z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.code}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const number = value => Number(value || 0).toLocaleString('ko-KR');
const roleNames = { ui: '화면', api: 'API · 요청', core: '핵심 로직', test: '테스트', docs: '문서', config: '설정', script: '스크립트' };

document.querySelector('#app').innerHTML = `
  <div id="world" class="world"></div>
  <aside class="sidebar" id="sidebar">
    <a class="brand" href="#" aria-label="Code Atlas 전체 보기"><span class="brand-mark">${icon('cube')}</span><span>CODE ATLAS<small>저장소를 걷다</small></span></a>
    <div class="repo-heading"><span class="eyebrow">YOUR REPOSITORY</span><h1>DEMOS_CODEX<span class="status-dot" title="로컬 저장소 스냅샷"></span></h1><p>구조를 보고, 연결을 따라가세요.</p></div>
    <div class="stats"><div><strong id="file-count">—</strong><span>파일</span></div><div><strong id="line-count">—</strong><span>전체 줄</span></div><div><strong id="district-count">—</strong><span>구역</span></div></div>
    <label class="search-box">${icon('search')}<input id="search" type="search" placeholder="파일 · 경로 · 함수 검색" aria-label="파일, 경로, 함수 검색" autocomplete="off" /><kbd>/</kbd></label>
    <div class="nav-heading"><span id="nav-title">구역 탐색</span><span id="nav-count"></span></div>
    <div class="nav-scroll"><nav id="district-list" aria-label="기능별 구역"></nav><div id="search-results" class="search-results" hidden></div></div>
    <div class="sidebar-bottom"><button id="example-launch" class="tour-launch example-launch">${icon('layers')}<span>요청 데이터 예제<small>텍스트 · 이미지 · Hermes API</small></span>${icon('arrow')}</button><button id="tour-launch" class="tour-launch">${icon('tour')}<span>코드 탐색 가이드<small id="guide-count">구역별 역할과 동작 따라가기</small></span>${icon('arrow')}</button><div class="snapshot-note"><span class="status-dot"></span><span id="snapshot-time">저장소 분석 중</span><button id="scope-button" aria-label="분석 범위 보기">${icon('info')}</button></div></div>
  </aside>
  <header class="toolbar"><div class="location"><button id="menu-button" class="icon-button" aria-label="구역 탐색 메뉴">${icon('menu')}</button><span class="eyebrow">EXPLORING</span><strong id="location-name">전체 저장소</strong></div><div class="toolbar-actions"><div class="mode-switch" role="group" aria-label="카메라 모드"><button id="orbit-button" class="active">${icon('orbit')}<span>조감도</span></button><button id="walk-button">${icon('walk')}<span>걷기</span></button></div><button id="overview-button" class="icon-button" aria-label="전체 보기" title="전체 보기">${icon('reset')}</button></div></header>
  <div id="loading" class="loading"><span class="loading-orbit"></span><strong>저장소를 공간으로 펼치는 중</strong><p>실제 파일과 연결 관계를 준비하고 있습니다.</p></div>
  <div class="map-caption" id="map-caption"><span class="eyebrow">A MAP OF YOUR CODE</span><h2>코드 사이를<br/>걸어보세요.</h2><p>구역을 선택하면 역할과 핵심 코드가 보입니다.<br/>코드 탐색 가이드로 동작을 따라가세요.</p></div>
  <div id="hover-path" class="hover-path" hidden></div>
  <section id="inspector" class="inspector" aria-label="선택한 파일 정보" hidden></section>
  <section id="district-panel" class="inspector district-panel" aria-label="구역 역할과 주요 코드" hidden></section>
  <section id="tour-panel" class="tour-panel" aria-label="코드 탐색 경로" hidden></section>
  <div id="walk-prompt" class="walk-prompt" hidden><span>${icon('walk')}</span><strong>지도를 클릭해 걷기 시작</strong><p>W A S D 이동 · 마우스로 둘러보기 · Esc 해제</p><button id="resume-walk">걷기 시작 ${icon('arrow')}</button></div>
  <div id="crosshair" class="crosshair" hidden><i></i><i></i></div>
  <div class="bottom-bar"><div class="legend" id="legend"><span>FILE TYPE</span></div><div class="map-controls" id="control-hint"><span>드래그 <b>회전</b></span><span>휠 <b>확대</b></span><span>우클릭 <b>이동</b></span></div></div>
  <div class="minimap-wrap"><div class="minimap-title"><span>${icon('compass')}OVERVIEW</span><span id="fps">— FPS</span></div><canvas id="minimap" width="360" height="220" aria-label="현재 위치를 보여주는 미니맵"></canvas></div>
  <div id="toast" class="toast" role="status" hidden></div>
  <dialog id="scope-dialog" class="scope-dialog"><div class="dialog-heading"><span class="eyebrow">ABOUT THIS MAP</span><button id="scope-close" class="icon-button" aria-label="닫기">${icon('close')}</button></div><h2>실제 코드에서 만든 지도</h2><div id="scope-content"></div></dialog>
  <dialog id="guide-dialog" class="scope-dialog guide-dialog"><div class="dialog-heading"><span class="eyebrow">EXPLORE THE CODE</span><button id="guide-close" class="icon-button" aria-label="가이드 닫기">${icon('close')}</button></div><h2>어떤 동작을 이해할까요?</h2><p>실행 흐름은 확인된 호출을 따라갑니다. 읽기 경로는 문서·테스트·독립 데모의 이해 순서입니다.</p><div id="guide-list" class="guide-list"></div></dialog>
`;

let data, tour, world, guideIndex, scenarioIndex;
let debuggerUI;
let activeDistrict = null, selectedId = null, tourStep = -1, playing = false, playTimer;
let toastTimer, searchTimer;
const $ = selector => document.querySelector(selector);
const files = new Map(), districts = new Map(), outgoing = new Map(), incoming = new Map();

function toast(message) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3800);
}

function renderDistricts() {
  $('#district-list').innerHTML = `<button class="district-item all-districts ${activeDistrict ? '' : 'active'}" data-district=""><span class="district-icon">${icon('layers')}</span><span class="district-name">전체 저장소<small>모든 구역 한눈에 보기</small></span><span class="file-badge">${number(data.files.length)}</span></button>` +
    data.districts.map((district, index) => {
      const count = data.files.filter(f => f.district === district.id).length;
      return `<button class="district-item ${activeDistrict === district.id ? 'active' : ''}" data-district="${esc(district.id)}" title="${esc(guideIndex.districts.get(district.id)?.purpose || district.description)}"><span class="district-number" style="--district-color:${DISTRICT_COLORS[index % DISTRICT_COLORS.length]}">${`${index + 1}`.padStart(2, '0')}</span><span class="district-name">${esc(district.label)}<small>${esc(district.description)}</small></span><span class="file-badge">${number(count)}</span></button>`;
    }).join('');
  $('#nav-count').textContent = `${data.districts.length} DISTRICTS`;
  $('#district-list').querySelectorAll('button').forEach(button => button.addEventListener('click', () => navigateDistrict(button.dataset.district || null)));
  if ($('#search').value.trim()) searchFiles();
}

function navigateDistrict(id) {
  debuggerUI?.close();
  endTour(); closeInspector(); activeDistrict = id;
  world.focusDistrict(id); renderDistricts();
  $('#location-name').textContent = id ? districts.get(id)?.label || id : '전체 저장소';
  $('#map-caption').hidden = Boolean(id);
  $('#sidebar').classList.remove('open');
  renderDistrictGuide();
}

const tourKind = tour => tour.kind === 'example' ? '데이터 처리 예제' : tour.kind === 'reading' ? '읽기 경로' : '실행 흐름';

function evidenceMarkup(evidence) {
  const valid = (evidence || []).filter(item => files.has(item.path));
  return valid.length ? `<details class="source-evidence"><summary>코드 근거 ${valid.length}곳</summary>${valid.map(item => `<button data-evidence-file="${esc(item.path)}"><code>${esc(item.path)}:${item.line}</code><small>${esc(item.note)}</small></button>`).join('')}</details>` : '';
}

function bindEvidence(element) {
  element.querySelectorAll('[data-evidence-file]').forEach(button => button.addEventListener('click', () => {
    showSourceFile(button.dataset.evidenceFile);
  }));
}

function showSourceFile(id) {
  pauseTour();
  // A narrow screen has room for one explanation panel. File routes allow return.
  selectFile(id, true, tourStep >= 0 && tour?.kind !== 'example' && window.matchMedia('(min-width: 641px)').matches);
}

function renderDistrictGuide() {
  const district = districts.get(activeDistrict), guide = guideIndex?.districts.get(activeDistrict);
  $('#district-panel').hidden = !district || Boolean(selectedId) || world?.mode === 'walk' || tourStep >= 0;
  if ($('#district-panel').hidden) return;
  const route = guideIndex.tours.get(guide?.tourId);
  const keyFiles = (guide?.keyFiles || []).map(id => files.get(id)).filter(Boolean);
  $('#district-panel').innerHTML = `<div class="inspector-top"><span class="eyebrow">DISTRICT GUIDE</span><button id="district-close" class="icon-button" aria-label="구역 설명 닫기">${icon('close')}</button></div>
    <h2 class="district-guide-title">${esc(district.label)}</h2><code class="district-guide-path">${esc(district.path)}</code>
    <div class="inspector-scroll district-guide-scroll"><p class="district-purpose">${esc(guide?.purpose || district.description)}</p>
    ${guide?.flow ? `<section class="file-section"><h3>어떻게 동작하나요?</h3><p class="guide-flow">${esc(guide.flow)}</p></section>` : ''}
    <section class="file-section"><h3>먼저 볼 주요 코드 <span>${keyFiles.length}</span></h3>${keyFiles.map(file => `<button class="key-file" data-file="${esc(file.id)}"><strong>${esc(file.name)}${icon('chevron')}</strong><code>${esc(file.path)}</code><small>${esc(guideIndex.files.get(file.id)?.purpose || file.description)}</small></button>`).join('')}</section></div>
    ${route ? `<button id="district-tour" class="walk-near-file" data-tour="${esc(route.id)}">${icon('tour')}이 구역 따라가기 <small>${route.steps.length}단계 · ${tourKind(route)}</small>${icon('arrow')}</button>` : ''}`;
  $('#district-close').addEventListener('click', () => { $('#district-panel').hidden = true; });
  $('#district-panel').querySelectorAll('[data-file]').forEach(button => button.addEventListener('click', () => selectFile(button.dataset.file)));
  $('#district-tour')?.addEventListener('click', event => startTour(event.currentTarget.dataset.tour));
}

function openGuideCatalog() {
  debuggerUI?.close();
  world.unlock(); pauseTour();
  $('#guide-dialog h2').textContent = '어떤 동작을 이해할까요?';
  $('#guide-dialog > p').textContent = '실행 흐름은 확인된 호출을 따라갑니다. 읽기 경로는 문서·테스트·독립 데모의 이해 순서입니다.';
  const orderedTours = data.districts.flatMap(district => [...guideIndex.tours.values()].filter(route => route.districtId === district.id));
  $('#guide-list').innerHTML = orderedTours.map(route => `<button class="guide-option" data-tour="${esc(route.id)}"><span class="guide-option-meta">${esc(districts.get(route.districtId)?.label)}<i>${tourKind(route)} · ${route.steps.length}단계</i></span><strong>${esc(route.title)}${icon('arrow')}</strong><small>${esc(route.description)}</small></button>`).join('');
  $('#guide-list').querySelectorAll('[data-tour]').forEach(button => button.addEventListener('click', () => {
    $('#guide-dialog').close(); startTour(button.dataset.tour);
  }));
  $('#guide-dialog').showModal(); $('#guide-list').scrollTop = 0; $('#sidebar').classList.remove('open');
}

function openScenarioCatalog() {
  debuggerUI?.close();
  world.unlock(); pauseTour();
  $('#guide-dialog h2').textContent = '요청 데이터는 어떻게 바뀔까요?';
  $('#guide-dialog > p').textContent = '가상 입력과 응답으로 실제 코드의 처리 경로를 따라갑니다. 이미지를 첨부하는 경우와 API를 호출하는 경우를 비교해 보세요.';
  $('#guide-list').innerHTML = [...scenarioIndex.values()].map(example => `<button class="guide-option scenario-option" data-scenario="${esc(example.id)}"><span class="guide-option-meta">${example.category === 'query' ? 'QUERY · 사용자 질문' : 'HERMES · API와 웹 채팅'}<i>데이터 예제 · ${example.steps.length}단계</i></span><strong>${esc(example.title)}${icon('arrow')}</strong><small>${esc(example.description)}</small></button>`).join('');
  $('#guide-list').querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', () => {
    $('#guide-dialog').close(); startScenario(button.dataset.scenario);
  }));
  $('#guide-dialog').showModal(); $('#guide-list').scrollTop = 0; $('#sidebar').classList.remove('open');
}

function startScenario(id) {
  endTour(); tour = scenarioIndex.get(id);
  if (!tour) return;
  $('#district-panel').hidden = true;
  openTour(0);
}

function scenarioMarkup(example, step) {
  const peers = [...scenarioIndex.values()].filter(item => item.category === example.category);
  const attachments = example.input.attachments || [];
  return `<div class="scenario-switch" role="group" aria-label="입력 예제 비교">${peers.map(item => `<button data-scenario="${esc(item.id)}" aria-pressed="${example.id === item.id}">${esc(item.label || item.title)}</button>`).join('')}</div>
    <div class="scenario-input"><span>가상 입력</span><p>${esc(example.input.query)}</p><small>${attachments.length ? `이미지 첨부: ${esc(attachments.map(item => item.name).join(', '))}` : '첨부 파일 없음'}</small>${attachments.filter(item => item.previewText).map(item => `<div class="image-example" role="img" aria-label="가상 오류 화면 ${esc(item.name)}"><span>${esc(item.name)} · 이미지 내용 예시</span><code>${esc(item.previewText)}</code></div>`).join('')}</div>
    <p class="scenario-note">코드로 확인한 경로 · 데이터와 응답은 이해를 돕는 예시입니다.</p><details class="scenario-assumption"><summary>이 예제의 조건</summary><p>${esc(example.description)}</p></details>
    <nav class="data-flow" aria-label="요청 데이터 처리 단계">${example.steps.map((item, index) => `<button data-flow-step="${index}" class="${index === tourStep ? 'current' : index < tourStep ? 'visited' : ''}" ${index === tourStep ? 'aria-current="step"' : ''} title="${esc(item.title)} · ${esc(item.fileId)}"><span>${`${index + 1}`.padStart(2, '0')}</span><strong>${esc(item.title.replace(/^\d+\s*[·.)]\s*/, ''))}</strong></button>`).join('')}</nav>
    <div class="data-transform"><section><h3>들어오는 데이터</h3><p>${esc(step.input)}</p></section><section><h3>이 단계의 처리</h3><p>${esc(step.processing)}</p></section><section><h3>다음으로 전달</h3><p>${esc(step.output)}</p></section></div>
    <details class="payload-details" open><summary>이 단계 데이터 · JSON 예시</summary><pre>${esc(examplePayload(step.payload))}</pre></details>
    ${tourStep === example.steps.length - 1 && example.result ? `<div class="scenario-result"><strong>예시 결과</strong><p>${esc(example.result.description)}</p><details class="payload-details"><summary>예시 응답 보기</summary><pre>${esc(examplePayload(example.result.payload))}</pre></details></div>` : ''}`;
}

function startTour(id, index = 0) {
  endTour(); tour = guideIndex.tours.get(id);
  if (!tour) return;
  $('#district-panel').hidden = true;
  openTour(index);
}

function searchFiles() {
  const query = $('#search').value.trim().toLowerCase();
  $('#district-list').hidden = Boolean(query); $('#search-results').hidden = !query;
  $('#nav-title').textContent = query ? '검색 결과' : '구역 탐색';
  if (!query) { $('#nav-count').textContent = `${data.districts.length} DISTRICTS`; return; }
  const matches = data.files.filter(file =>
    file.path.toLowerCase().includes(query) || (file.symbols || []).some(s => s.name.toLowerCase().includes(query)));
  $('#nav-count').textContent = `${number(matches.length)} FILES`;
  const top = matches.sort((a, b) => Number(b.name.toLowerCase() === query) - Number(a.name.toLowerCase() === query) ||
    Number(b.name.toLowerCase().startsWith(query)) - Number(a.name.toLowerCase().startsWith(query)) || a.path.localeCompare(b.path)).slice(0, 40);
  $('#search-results').innerHTML = top.length ? top.map(file => `<button class="search-result" data-file="${esc(file.id)}"><span class="language-dot" style="background:${LANGUAGE_COLORS[file.language] || LANGUAGE_COLORS.Other}"></span><span><strong>${esc(file.name)}</strong><small>${esc(file.path)}</small></span>${icon('chevron')}</button>`).join('') +
    (matches.length > 40 ? '<p class="result-note">상위 40개 표시 · 경로나 함수명으로 좁혀보세요.</p>' : '') : '<div class="empty-results">일치하는 파일이 없습니다.<small>파일 이름이나 경로의 일부로 검색해 보세요.</small></div>';
  $('#search-results').querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
    endTour(); selectFile(button.dataset.file); $('#sidebar').classList.remove('open'); $('#search').blur();
  }));
}

function relationList(edges, direction) {
  if (!edges.length) return '<p class="empty-note">확인된 내부 import 관계가 없습니다.</p>';
  return edges.slice(0, 40).map(edge => {
    const id = direction === 'out' ? edge.target : edge.source;
    const file = files.get(id);
    return `<button class="dependency-item" data-file="${esc(id)}"><span class="language-dot" style="background:${LANGUAGE_COLORS[file?.language] || LANGUAGE_COLORS.Other}"></span><span><strong>${esc(file?.name || id)}</strong><small>${esc(id)}</small></span><span class="dependency-direction">${direction === 'out' ? '↗' : '↙'}</span></button>`;
  }).join('') + (edges.length > 40 ? `<p class="result-note">전체 ${number(edges.length)}개 중 40개 표시</p>` : '');
}

function selectFile(id, focus = true, keepTour = false) {
  const file = files.get(id); if (!file) return;
  if (!keepTour) endTour();
  selectedId = id;
  activeDistrict = file.district; world.visibleDistrict = null;
  world.selectFile(id, focus);
  $('#district-panel').hidden = true;
  $('#map-caption').hidden = true; $('#walk-prompt').hidden = true;
  $('#inspector').hidden = false;
  const refs = outgoing.get(id) || [], usedBy = incoming.get(id) || [];
  const symbols = (file.symbols || []).slice(0, 20);
  const unknown = data.unresolved.filter(item => item.source === id).length;
  const languageColor = LANGUAGE_COLORS[file.language] || LANGUAGE_COLORS.Other;
  const explanation = guideIndex.files.get(id);
  const fileTours = toursForFile(guideIndex, id);
  const districtGuide = guideIndex.districts.get(file.district);
  const groupTour = guideIndex.tours.get(districtGuide?.tourId);
  const routes = fileTours.length ? fileTours : groupTour ? [groupTour] : [];
  $('#inspector').innerHTML = `<div class="inspector-top"><span class="eyebrow">FILE INSPECTOR</span><button id="inspector-close" class="icon-button" aria-label="파일 정보 닫기">${icon('close')}</button></div>
    <div class="file-title"><span class="file-type-icon" style="color:${languageColor}">${icon('code')}</span><h2>${esc(file.name)}</h2></div>
    <div class="file-path"><code>${esc(file.path)}</code><button id="copy-path" class="icon-button" aria-label="파일 경로 복사" title="파일 경로 복사">${icon('copy')}</button></div>
    <div class="file-tags"><span style="color:${languageColor}"><i style="background:${languageColor}"></i>${esc(file.language)}</span><span>${esc(roleNames[file.role] || file.role)}</span><button id="file-district" class="file-district">구역 역할 보기 ${icon('chevron')}</button></div>
    <p class="file-description">${esc(explanation?.purpose || file.description)}</p>
    <div class="file-metrics"><div><strong>${number(file.lines)}</strong><span>전체 줄</span></div><div><strong>${number(file.nonEmptyLines)}</strong><span>비어 있지 않은 줄</span></div><div><strong>${number(file.symbols?.length)}</strong><span>주요 선언</span></div></div>
    <div class="inspector-scroll">
    ${explanation ? `<section class="file-section behavior-section"><h3>이 코드가 하는 일 <span>코드 확인</span></h3><dl class="behavior-details"><dt>입력</dt><dd>${esc(explanation.input)}</dd><dt>처리</dt><dd>${esc(explanation.processing)}</dd><dt>결과</dt><dd>${esc(explanation.output)}</dd></dl>${explanation.symbols?.length ? `<div class="explained-symbols">${explanation.symbols.map(symbol => `<div><strong>${esc(symbol.name)} <span>L${symbol.line}</span></strong><p>${esc(symbol.description)}</p></div>`).join('')}</div>` : ''}${evidenceMarkup(explanation.evidence)}</section>` : `<section class="file-section"><h3>구역 안에서의 역할</h3><p class="guide-flow">${esc(districtGuide?.purpose || districts.get(file.district)?.description)}</p><p class="result-note">이 파일의 설명은 구조 분석 기준입니다. 구역의 주요 코드는 입력·처리·결과와 코드 근거를 추가로 확인했습니다.</p></section>`}
    ${routes.length ? `<section class="file-section"><h3>${fileTours.length ? '이 코드가 등장하는 경로' : '이 구역을 이해하는 경로'}</h3>${routes.map(route => `<button class="file-route" data-tour="${esc(route.id)}" data-step="${Math.max(0, route.steps.findIndex(step => step.fileId === id))}"><strong>${esc(route.title)}${icon('arrow')}</strong><small>${tourKind(route)} · ${route.steps.length}단계${fileTours.length ? ' · 이 코드부터 보기' : ''}</small></button>`).join('')}</section>` : ''}
    ${symbols.length ? `<section class="file-section"><h3>주요 선언 <span>${number(file.symbols.length)}</span></h3><div class="symbol-list">${symbols.map(symbol => `<div><span class="symbol-kind">${symbol.kind === 'class' ? 'C' : symbol.kind === 'function' ? 'ƒ' : 'D'}</span><code>${esc(symbol.name)}</code><span class="line-number">L${symbol.line}</span></div>`).join('')}</div>${file.symbols.length > 20 ? '<p class="result-note">상위 20개 선언 표시</p>' : ''}</section>` : ''}
    <section class="file-section"><h3><i class="relation-dot out"></i>참조하는 파일 <span>${refs.length}</span></h3>${relationList(refs, 'out')}</section>
    <section class="file-section"><h3><i class="relation-dot in"></i>이 파일을 참조 <span>${usedBy.length}</span></h3>${relationList(usedBy, 'in')}</section>
    <p class="analysis-note">연결선은 정적 import 방향입니다.${file.language === 'JavaScript' || file.language === 'TypeScript' ? ' JS/TS는 휴리스틱 분석입니다.' : ''}${unknown ? ` 외부·미해결 참조 ${unknown}개.` : ''}${refs.length + usedBy.length > 60 ? ' 지도 연결선은 최대 60개 표시합니다.' : ''}</p>
    </div><button id="walk-near-file" class="walk-near-file">${icon('walk')}이 건물 옆에서 걷기${icon('arrow')}</button>`;
  $('#inspector-close').addEventListener('click', closeInspector);
  $('#file-district').addEventListener('click', () => navigateDistrict(file.district));
  bindEvidence($('#inspector'));
  $('#inspector').querySelectorAll('[data-tour]').forEach(button => button.addEventListener('click', () => startTour(button.dataset.tour, Number(button.dataset.step))));
  $('#copy-path').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(file.path); toast('파일 경로를 복사했습니다.'); }
    catch { toast(file.path); }
  });
  $('#walk-near-file').addEventListener('click', () => {
    endTour(); closeInspector(); world.startWalk();
  });
  $('#inspector').querySelectorAll('[data-file]').forEach(button => button.addEventListener('click', () => selectFile(button.dataset.file)));
  $('#location-name').textContent = districts.get(file.district)?.label || '파일 탐색';
  renderDistricts(); drawMinimap();
}

function closeInspector() {
  selectedId = null; $('#inspector').hidden = true; world?.clearSelection();
  if (world?.mode === 'walk') $('#walk-prompt').hidden = world.isWalking;
  renderDistrictGuide();
}

function openTour(index = 0) {
  if (!tour?.steps?.length) return;
  tourStep = Math.max(0, Math.min(tour.steps.length - 1, index));
  const step = tour.steps[tourStep];
  const example = tour.kind === 'example';
  selectFile(step.fileId, true, true);
  $('#tour-panel').classList.toggle('example-mode', example);
  if (example) { $('#inspector').hidden = true; world.clearConnections(); }
  $('#tour-panel').hidden = false;
  $('#tour-panel').innerHTML = `<div class="tour-progress">${tour.steps.map((_, i) => `<i class="${i <= tourStep ? 'visited' : ''}"></i>`).join('')}</div><div class="tour-header"><span class="tour-step">${`${tourStep + 1}`.padStart(2, '0')}<small>/ ${`${tour.steps.length}`.padStart(2, '0')}</small></span><div><span class="eyebrow">${esc(tour.title)}</span><h2>${esc(step.title.replace(/^\d+\s*[·.)]\s*/, ''))}</h2></div><button id="tour-close" class="icon-button" aria-label="투어 종료">${icon('close')}</button></div><div class="tour-context"><span>${tourKind(tour)}</span><span>${esc(districts.get(files.get(step.fileId)?.district)?.label)}</span><button id="tour-catalog">${example ? '다른 예제 선택' : '다른 경로 선택'} ${icon('chevron')}</button></div>${example ? scenarioMarkup(tour, step) : ''}<p class="tour-description">${esc(step.description)}</p>${evidenceMarkup(step.evidence)}
    <div class="tour-footer"><button class="tour-source" id="tour-source" aria-label="파일 설명 보기: ${esc(step.fileId)}:${step.line}" title="파일 설명 보기">${icon('code')}<span>${esc(step.fileId)}:${step.line}</span></button><div class="tour-navigation"><button id="tour-prev" class="icon-button" aria-label="이전 단계" ${tourStep === 0 ? 'disabled' : ''}>${icon('previous')}</button><button id="tour-play" class="icon-button" aria-label="${playing ? '자동 재생 일시정지' : '자동 재생'}">${icon(playing ? 'pause' : 'tour')}</button><button id="tour-next" class="next-step">${tourStep === tour.steps.length - 1 ? '투어 마치기' : '다음 단계'}${icon('arrow')}</button></div></div>`;
  $('#tour-close').addEventListener('click', endTour);
  $('#tour-catalog').addEventListener('click', example ? openScenarioCatalog : openGuideCatalog);
  if (example) {
    $('#tour-panel').scrollTop = 0;
    $('#tour-panel').querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', () => startScenario(button.dataset.scenario)));
    $('#tour-panel').querySelectorAll('[data-flow-step]').forEach(button => button.addEventListener('click', () => {
      pauseTour(); openTour(Number(button.dataset.flowStep));
    }));
    if (tourStep === tour.steps.length - 1) $('#tour-next').childNodes[0].textContent = '예제 마치기';
    $('#tour-panel .data-flow [aria-current="step"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  bindEvidence($('#tour-panel'));
  $('#tour-prev').addEventListener('click', () => { pauseTour(); openTour(tourStep - 1); });
  $('#tour-next').addEventListener('click', () => { pauseTour(); tourStep < tour.steps.length - 1 ? openTour(tourStep + 1) : endTour(); });
  $('#tour-source').addEventListener('click', () => showSourceFile(step.fileId));
  $('#tour-play').addEventListener('click', () => {
    if (playing) pauseTour();
    else { playing = true; scheduleTour(); }
    openTour(tourStep);
  });
}

function scheduleTour() {
  clearTimeout(playTimer);
  playTimer = setTimeout(() => {
    if (!playing) return;
    if (tourStep < tour.steps.length - 1) { openTour(tourStep + 1); scheduleTour(); }
    else { pauseTour(); openTour(tourStep); }
  }, 15000);
}

function pauseTour() {
  playing = false; clearTimeout(playTimer);
  const button = $('#tour-play');
  if (button) { button.innerHTML = icon('tour'); button.setAttribute('aria-label', '자동 재생'); }
}

function endTour() {
  const example = tour?.kind === 'example';
  pauseTour(); tourStep = -1; tour = null; $('#tour-panel').hidden = true; $('#tour-panel').classList.remove('example-mode');
  if (example && selectedId) { $('#inspector').hidden = false; world?.renderConnections(selectedId); }
}

function drawMinimap() {
  if (!world) return;
  const canvas = $('#minimap'), ctx = canvas.getContext('2d');
  const layout = world.layout, padding = 14;
  const scale = Math.min((canvas.width - padding * 2) / layout.width, (canvas.height - padding * 2) / layout.depth);
  const offsetX = (canvas.width - layout.width * scale) / 2, offsetY = (canvas.height - layout.depth * scale) / 2;
  const point = (x, z) => [(x + layout.width / 2) * scale + offsetX, (z + layout.depth / 2) * scale + offsetY];
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const district of layout.districts) {
    const [x, y] = point(district.x, district.z);
    ctx.fillStyle = activeDistrict === district.id ? '#345247' : '#273943';
    ctx.fillRect(x, y, district.width * scale, district.depth * scale);
    ctx.strokeStyle = DISTRICT_COLORS[district.index % DISTRICT_COLORS.length] + '99';
    ctx.lineWidth = 1; ctx.strokeRect(x, y, district.width * scale, district.depth * scale);
  }
  for (const building of layout.buildings) {
    const [x, y] = point(building.x, building.z);
    ctx.fillStyle = LANGUAGE_COLORS[building.file.language] + '77';
    ctx.fillRect(x - .65, y - .65, 1.3, 1.3);
  }
  if (selectedId) {
    const building = layout.byId.get(selectedId);
    if (building) {
      const [x, y] = point(building.x, building.z);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.stroke();
    }
  }
  if (world.mode === 'walk') {
    const [x, y] = point(world.camera.position.x, world.camera.position.z);
    ctx.fillStyle = '#c3ffe5'; ctx.beginPath(); ctx.arc(x, y, 3.4, 0, Math.PI * 2); ctx.fill();
  }
}

function setMode(mode) {
  const walking = mode === 'walk';
  $('#orbit-button').classList.toggle('active', !walking); $('#walk-button').classList.toggle('active', walking);
  $('#walk-prompt').hidden = !walking || world?.isWalking;
  $('#map-caption').hidden = walking || Boolean(selectedId) || Boolean(activeDistrict);
  $('#control-hint').innerHTML = walking ? '<span><b>W A S D</b> 이동</span><span>마우스 <b>시점</b></span><span><b>Esc</b> 해제</span>' : '<span>드래그 <b>회전</b></span><span>휠 <b>확대</b></span><span>우클릭 <b>이동</b></span>';
  document.body.classList.toggle('walking', walking);
  if (walking) $('#district-panel').hidden = true;
  drawMinimap();
}

async function initialize() {
  try {
    const [mapResponse, tourResponse, coreResponse, edgeResponse, queryResponse, apiResponse] = await Promise.all([
      fetch(`${import.meta.env.BASE_URL}repo-map.json`), fetch(`${import.meta.env.BASE_URL}tour.json`),
      fetch(`${import.meta.env.BASE_URL}guides-core.json`), fetch(`${import.meta.env.BASE_URL}guides-edge.json`),
      fetch(`${import.meta.env.BASE_URL}scenarios-query.json`), fetch(`${import.meta.env.BASE_URL}scenarios-api.json`),
    ]);
    if (!mapResponse.ok) throw new Error('지도 데이터 파일을 읽지 못했습니다. npm run scan으로 생성해 주세요.');
    data = await mapResponse.json();
    const originalTour = tourResponse.ok ? await tourResponse.json() : null;
    const packs = await Promise.all([coreResponse, edgeResponse].map(response => response.ok ? response.json() : null));
    const scenarioPacks = await Promise.all([queryResponse, apiResponse].map(response => response.ok && response.headers.get('content-type')?.includes('json') ? response.json() : null));
    if (!data.files?.length || !data.districts?.length) throw new Error('분석된 소스 파일이 없습니다.');
    data.edges ||= []; data.unresolved ||= [];
    data.files.forEach(file => files.set(file.id, file));
    data.districts.forEach(district => districts.set(district.id, district));
    guideIndex = createGuideIndex(data, packs, originalTour);
    scenarioIndex = createScenarioIndex(data, scenarioPacks);
    $('#guide-count').textContent = `${guideIndex.districts.size}개 구역 · ${guideIndex.tours.size}개 탐색 경로`;
    const uniqueRelationships = [...new Map(data.edges.map(edge => [`${edge.source}\0${edge.target}`, edge])).values()];
    uniqueRelationships.forEach(edge => {
      if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      outgoing.get(edge.source).push(edge); incoming.get(edge.target).push(edge);
    });
    $('#file-count').textContent = number(data.files.length);
    $('#line-count').textContent = data.summary.totalLines >= 1000000 ? `${(data.summary.totalLines / 1000000).toFixed(1)}M` : `${Math.round(data.summary.totalLines / 1000)}k`;
    $('#line-count').title = `${number(data.summary.totalLines)}줄`;
    $('#district-count').textContent = number(data.districts.length);
    const generated = new Date(data.generatedAt);
    $('#snapshot-time').textContent = `로컬 스냅샷 · ${new Intl.DateTimeFormat('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Seoul' }).format(generated)}`;
    const languageCounts = {};
    data.files.forEach(file => { languageCounts[file.language] = (languageCounts[file.language] || 0) + 1; });
    const languages = Object.entries(languageCounts).sort((a, b) => b[1] - a[1]).filter(([, count]) => count > 3);
    $('#legend').innerHTML += languages.slice(0, 5).map(([language]) => `<span><i style="background:${LANGUAGE_COLORS[language] || LANGUAGE_COLORS.Other}"></i>${esc(language)}</span>`).join('') + `<button id="legend-info" aria-label="지도 범례와 분석 범위">높이 = 줄 수 ${icon('info')}</button>`;
    $('#legend-info').addEventListener('click', () => $('#scope-dialog').showModal());
    $('#scope-content').innerHTML = `<p>선정한 소스·문서 <strong>${number(data.files.length)}개</strong>를 기능별 <strong>${data.districts.length}개 구역</strong>에 배치했습니다. 생성 시점의 스냅샷이며 다시 분석하면 현재 파일을 반영합니다.</p><dl><dt>건물</dt><dd>파일 하나 · 언어별 색상</dd><dt>높이</dt><dd>전체 줄 수를 로그 스케일로 변환 · 최대 높이 제한</dd><dt>연결선</dt><dd><i class="relation-dot out"></i>참조하는 파일 <i class="relation-dot in"></i>이 파일을 참조하는 파일</dd><dt>의존성</dt><dd>Python은 AST 분석, JS/TS는 휴리스틱 분석. 외부·미해결 import는 내부 연결선에 포함하지 않습니다.</dd><dt>파일 설명</dt><dd>주요 파일은 코드를 확인해 입력·처리·결과와 근거를 설명합니다. 나머지 파일에는 구조 분석 설명과 구역의 역할을 표시합니다.</dd><dt>투어</dt><dd>모든 구역의 탐색 경로와 기존 채팅 여정을 제공합니다. 확인한 호출은 실행 흐름으로, 독립 파일·문서·테스트의 이해 순서는 읽기 경로로 표시합니다. 각 단계에 코드 근거가 있습니다.</dd></dl><p class="analysis-note">지도 메타데이터에는 코드 본문을 넣지 않습니다. 디버깅 화면은 허용된 원본 파일만 읽기 전용으로 표시합니다. 사용자 데이터·비밀정보 파일·백업·런타임·의존성·외부 스킬 라이브러리·검증 산출물은 제외합니다. 일부 미지원 언어의 의존성은 분석하지 않습니다.</p>`;
    renderDistricts();
    world = new AtlasWorld($('#world'), data, {
      onSelect: id => selectFile(id, false),
      onDistrict: navigateDistrict,
      onHover: file => {
        $('#hover-path').hidden = !file || Boolean(selectedId);
        $('#hover-path').textContent = file?.path || '';
      },
      onMode: setMode, onToast: toast,
      onLock: locked => {
        $('#crosshair').hidden = !locked; $('#walk-prompt').hidden = locked || world?.mode !== 'walk';
        document.body.classList.toggle('pointer-locked', locked);
      },
      onMetrics: metrics => {
        $('#fps').textContent = `${metrics.fps} FPS`; drawMinimap();
      },
    });
    $('#loading').hidden = true;
    debuggerUI = createDebugger({
      toast,
      onLocation: event => {
        if (event.kind === 'outside') {
          world.clearExecution(); world.clearSelection();
          $('#location-name').textContent = '관찰 대상 밖에서 중단';
          return;
        }
        if (!files.has(event.fileId)) return;
        const changed = world.selected?.file.id !== event.fileId;
        activeDistrict = files.get(event.fileId).district;
        world.visibleDistrict = null;
        if (changed) world.selectFile(event.fileId, true);
        world.renderExecution(event);
        $('#location-name').textContent = `${districts.get(activeDistrict)?.label || ''} · 실행 위치`;
        $('#map-caption').hidden = true;
        if (changed) renderDistricts();
      },
      onClose: () => {
        world.clearExecution();
        if (selectedId) world.selectFile(selectedId, false); else world.clearSelection();
        $('#location-name').textContent = districts.get(activeDistrict)?.label || '전체 저장소';
        renderDistrictGuide();
      },
    });
    const debugLaunch = document.createElement('button');
    debugLaunch.id = 'debugger-launch'; debugLaunch.className = 'tour-launch debugger-launch';
    debugLaunch.innerHTML = `${icon('code')}<span>디버거 · Playback<small>실제 실행 · 변수 · 쿼리 녹화</small></span>${icon('arrow')}`;
    $('.sidebar-bottom').prepend(debugLaunch);
    debugLaunch.addEventListener('click', () => {
      endTour(); closeInspector(); $('#district-panel').hidden = true;
      $('#sidebar').classList.remove('open'); world.unlock(); world.setMode('orbit');
      debuggerUI.open();
    });
    drawMinimap();
    $('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchFiles, 100); });
    $('#search').addEventListener('keydown', event => {
      if (event.key === 'Enter') $('#search-results button')?.click();
      if (event.key === 'Escape') { $('#search').value = ''; searchFiles(); $('#search').blur(); }
    });
    $('#overview-button').addEventListener('click', () => navigateDistrict(null));
    $('.brand').addEventListener('click', event => { event.preventDefault(); navigateDistrict(null); });
    $('#orbit-button').addEventListener('click', () => {
      world.unlock();
      if (world.mode === 'walk') { world.setMode('orbit'); selectedId ? world.selectFile(selectedId) : activeDistrict ? world.focusDistrict(activeDistrict) : world.overview(); }
      renderDistrictGuide();
    });
    $('#walk-button').addEventListener('click', () => {
      debuggerUI?.close();
      endTour(); closeInspector(); world.startWalk(); $('#sidebar').classList.remove('open');
    });
    $('#resume-walk').addEventListener('click', () => world.lockWalk());
    $('#tour-launch').addEventListener('click', openGuideCatalog);
    $('#example-launch').addEventListener('click', openScenarioCatalog);
    if (!scenarioIndex.size) $('#example-launch').hidden = true;
    if (!guideIndex.tours.size) $('#tour-launch').hidden = true;
    $('#guide-close').addEventListener('click', () => $('#guide-dialog').close());
    $('#guide-dialog').addEventListener('click', event => { if (event.target === $('#guide-dialog')) $('#guide-dialog').close(); });
    $('#scope-button').addEventListener('click', () => { world.unlock(); $('#scope-dialog').showModal(); });
    $('#scope-close').addEventListener('click', () => $('#scope-dialog').close());
    $('#scope-dialog').addEventListener('click', event => { if (event.target === $('#scope-dialog')) $('#scope-dialog').close(); });
    $('#menu-button').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
    document.addEventListener('keydown', event => {
      if (debuggerUI?.isOpen()) return;
      if (event.key === 'Escape' && document.querySelector('dialog[open]')) return;
      if (event.key === '/' && !event.target.matches('input, textarea') && !world.isWalking) {
        event.preventDefault(); $('#sidebar').classList.add('open'); $('#search').focus();
      }
      if (event.key === 'Escape' && !world.isWalking) {
        if (tourStep >= 0) endTour();
        else if (selectedId) closeInspector();
        $('#sidebar').classList.remove('open');
      }
    });
  } catch (error) {
    console.error(error);
    $('#loading').innerHTML = `<span class="error-mark">!</span><strong>지도를 준비하지 못했습니다</strong><p>${esc(error.message)}</p><button id="reload">다시 시도</button>`;
    $('#reload').addEventListener('click', () => location.reload());
  }
}

initialize();
