'use strict';
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const statuses = {new:'Inbox',shortlisted:'Shortlisted',applied:'Applied',interviewing:'Interviewing',offer:'Offer',rejected:'Rejected',archived:'Archived'};
const state = {jobs:[], sources:[], runs:[], profile:{profile:{},version:0}, view:'board', layout:'board', query:'', remote:'', reviewed:'', detail:null, profileDraft:null};
let noteTimer, pendingNote=null, noteChain=Promise.resolve(), toastTimer, focusBeforeDialog;
const token = $('meta[name="workspace-token"]').content;
async function api(path, options={}){
  const response = await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Workspace-Token':token,...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || 'Could not save your change.');
  return data;
}
function toast(message,error=false){const box=$('#toast');box.textContent=message;box.className=`toast visible${error?' error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.className='toast',4000);}
function report(error){toast(error.message || 'Something went wrong.',true);}
function shortDate(value){if(!value)return 'Not yet';const date=new Date(value);return Number.isNaN(date.valueOf())?value:date.toLocaleDateString(undefined,{month:'short',day:'numeric'});}
function salary(job){if(job.salary_text)return job.salary_text;if(job.salary_min!=null||job.salary_max!=null){const fmt=n=>n==null?'':Number(n).toLocaleString();return `${job.salary_currency||''} ${fmt(job.salary_min)}${job.salary_max!=null?' – '+fmt(job.salary_max):'+'}${job.salary_period?' / '+job.salary_period:''}`.trim();}return '';}
function pill(status){return `<span class="status-pill tone-${esc(status)}">${esc(statuses[status]||status)}</span>`;}
function profileReady(){const p=state.profile.profile;return Boolean(p.roles?.length||p.skills?.length||p.about||p.headline);}
async function refresh(render=true){
  const data=await api('/api/state');Object.assign(state,data);
  $('#count-all').textContent=state.jobs.length;
  $('#count-unreviewed').textContent=state.jobs.filter(j=>!j.reviewed_at).length;
  $('#count-shortlisted').textContent=state.jobs.filter(j=>j.review_status==='shortlisted').length;
  if(render) renderPage();
}
function navigate(view){
  state.view=view;state.query='';state.remote='';state.reviewed='';
  $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('#sidebar').classList.remove('mobile-open');
  $('#menu-button').setAttribute('aria-expanded','false');
  $('#breadcrumb-current').textContent=view==='profile'?'Search profile':view==='sources'?'Sources & searches':'Opportunities';
  renderPage();$('#main').scrollTop=0;
}
function heading(title,subtitle='',actions=''){
  return `<div class="page-heading"><div><h1>${esc(title)}</h1>${subtitle?`<p class="subtitle">${esc(subtitle)}</p>`:''}</div>${actions?`<div class="heading-actions">${actions}</div>`:''}</div>`;
}
function renderPage(){
  if(state.view==='profile')return renderProfile();
  if(state.view==='sources')return renderSources();
  const titles={board:'Opportunities',unreviewed:'To review',shortlisted:'Shortlisted',closed:'Closed opportunities'};
  $('#main').innerHTML=`<section class="page board-page">${heading(titles[state.view]||titles.board)}
    <div class="view-toolbar">
      <div class="view-tabs"><button class="view-tab ${state.layout==='board'?'selected':''}" data-layout="board">Board</button><button class="view-tab ${state.layout==='list'?'selected':''}" data-layout="list">List</button><span class="view-count" id="view-count"></span></div>
      <div class="toolbar-controls"><input id="job-search" type="search" aria-label="Search jobs" placeholder="Search…" value="${esc(state.query)}"><select class="filter-select" id="review-filter" aria-label="Filter reviewed jobs"><option value="">All reviews</option><option value="unreviewed">Not reviewed</option><option value="reviewed">Reviewed</option></select><button class="text-button" id="start-search">Search with Claude</button><button class="button primary" id="add-job">Add job</button></div>
    </div>
    <div id="job-content"></div></section>`;
  $('#start-search').onclick=openSearch;
  $('#add-job').onclick=()=>openAdd();
  $$('[data-layout]').forEach(el=>el.onclick=()=>{state.layout=el.dataset.layout;renderPage();});
  $('#job-search').oninput=e=>{state.query=e.target.value;renderJobs();};
  $('#review-filter').value=state.reviewed;
  $('#review-filter').onchange=e=>{state.reviewed=e.target.value;renderJobs();};
  renderJobs();
}
function filteredJobs(){return state.jobs.filter(j=>{
  if(state.view==='unreviewed'&&j.reviewed_at)return false;
  if(state.view==='shortlisted'&&j.review_status!=='shortlisted')return false;
  if(state.view==='closed'&&!['rejected','archived'].includes(j.review_status))return false;
  if(state.reviewed==='unreviewed'&&j.reviewed_at)return false;
  if(state.reviewed==='reviewed'&&!j.reviewed_at)return false;
  if(state.remote&&j.remote_status!==state.remote)return false;
  return !state.query||[j.title,j.company,j.location_text,...(j.skills||[]),...(j.review_tags||[])].join(' ').toLowerCase().includes(state.query.toLowerCase());
});}
function card(job){
  const details=[job.location_text,salary(job)].filter(Boolean);
  return `<article class="job-card" draggable="true" data-job="${esc(job.id)}">
    <button class="card-open" data-open-job="${esc(job.id)}">${esc(job.title)}</button>
    <div class="card-company">${esc(job.company)}</div>
    ${details.length?`<div class="card-details">${details.map(d=>`<div>${esc(d)}</div>`).join('')}</div>`:''}
    ${job.review_tags?.length?`<div class="card-tags">${job.review_tags.slice(0,3).map(t=>`<span class="mini-tag">${esc(t)}</span>`).join('')}</div>`:''}
    <div class="card-footer"><span class="review-state ${job.reviewed_at?'is-reviewed':''}">${job.reviewed_at?'Reviewed':'Not reviewed'}</span>${job.review_notes?'<span>Notes</span>':''}${job.comment_count?`<span>${job.comment_count} ${job.comment_count===1?'comment':'comments'}</span>`:''}</div>
  </article>`;
}
function renderJobs(){
  const jobs=filteredJobs();$('#view-count').textContent=jobs.length;
  if(state.layout==='list'){
    $('#job-content').innerHTML=jobs.length?`<div class="table-wrap"><table class="jobs-table"><thead><tr><th>Job</th><th>Company</th><th>Status</th><th>Location</th><th>Review</th><th>Comments</th></tr></thead><tbody>${jobs.map(j=>`<tr><td><button class="table-title" data-open-job="${esc(j.id)}">${esc(j.title)}</button></td><td>${esc(j.company)}</td><td>${pill(j.review_status)}</td><td>${esc(j.location_text||'Not specified')}</td><td>${j.reviewed_at?'Reviewed':'Not reviewed'}</td><td>${j.comment_count||'—'}</td></tr>`).join('')}</tbody></table></div>`:`<div class="no-results">${state.jobs.length?'No jobs match these filters.':'No jobs yet. Add a job or search with Claude.'}</div>`;
  }else{
    const columns=state.view==='shortlisted'?['shortlisted']:state.view==='closed'?['rejected','archived']:['new','shortlisted','applied','interviewing','offer',...(['rejected','archived'].filter(s=>jobs.some(j=>j.review_status===s)))];
    $('#job-content').innerHTML=`${state.jobs.length&&jobs.length===0?'<p class="filter-empty" role="status">No jobs match these filters.</p>':''}<div class="board" aria-label="Job application board">${columns.map(status=>{const lane=jobs.filter(j=>j.review_status===status);return `<section class="column" data-column="${status}" aria-label="${statuses[status]} column"><div class="column-heading">${pill(status)}<span class="column-count">${lane.length}</span><button class="text-button column-add" data-add-status="${status}" aria-label="Add job to ${statuses[status]}">+</button></div><div class="column-body">${lane.map(card).join('')}<button class="new-card" data-add-status="${status}">+ New</button></div></section>`;}).join('')}</div>${state.jobs.length===0?'<p class="empty-board-hint">Add a job to get started, or use Search with Claude to collect listings.</p>':''}`;
    $$('[data-add-status]').forEach(el=>el.onclick=()=>openAdd(el.dataset.addStatus));
    $$('.job-card').forEach(el=>{
      el.ondragstart=e=>{e.dataTransfer.setData('text/plain',el.dataset.job);e.dataTransfer.effectAllowed='move';el.classList.add('dragging');};
      el.ondragend=()=>{$$('.dragging,.drag-over').forEach(n=>n.classList.remove('dragging','drag-over'));};
      el.onclick=e=>{if(!e.target.closest('button'))openJob(el.dataset.job).catch(report);};
    });
    $$('[data-column]').forEach(el=>{
      el.ondragover=e=>{e.preventDefault();el.classList.add('drag-over');};
      el.ondragleave=e=>{if(!el.contains(e.relatedTarget))el.classList.remove('drag-over');};
      el.ondrop=e=>{e.preventDefault();el.classList.remove('drag-over');const id=e.dataTransfer.getData('text/plain');if(state.jobs.some(j=>j.id===id))saveReview(id,{status:el.dataset.column}).catch(report);};
    });
  }
  $$('[data-open-job]').forEach(el=>el.onclick=()=>openJob(el.dataset.openJob).catch(report));
}
async function openJob(id){
  await flushNotes();state.detail=await api(`/api/jobs/${id}`);renderDetail();
  const dialog=$('#job-dialog');if(!dialog.open){focusBeforeDialog=document.activeElement;dialog.showModal();}
}
function renderDetail(){
  const job=state.detail,r=job.review;
  const property=(name,value)=>`<div class="property-row"><span class="property-label">${name}</span><span class="property-value">${esc(value||'Not specified')}</span></div>`;
  $('#job-detail').innerHTML=`<div class="drawer-toolbar"><span>Job details</span><button class="text-button" id="close-job" aria-label="Close job details">Close</button></div>
    <div class="drawer-content"><div class="detail-company">${esc(job.company)}</div><h2 class="detail-title">${esc(job.title)}</h2>
    <div class="detail-actions"><select id="detail-status" aria-label="Job status">${Object.entries(statuses).map(([k,v])=>`<option value="${k}" ${r.status===k?'selected':''}>${v}</option>`).join('')}</select><button class="button ${r.reviewed_at?'reviewed-button':''}" id="mark-reviewed" aria-pressed="${Boolean(r.reviewed_at)}">${r.reviewed_at?'Reviewed':'Mark reviewed'}</button><a class="button" href="${esc(job.canonical_url)}" target="_blank" rel="noopener noreferrer">Open listing</a></div>
    <div class="detail-properties">${property('Location',job.location_text)}${property('Compensation',salary(job))}${property('Work setup',job.remote_status==='unknown'?'Not specified':job.remote_status)}${property('Employment',job.employment_type?.replaceAll('_',' '))}${property('Eligibility',job.eligible_countries?.join(', ')||job.timezone_requirements)}</div>
    <section class="detail-section"><div class="section-title-row"><h3>Your notes</h3><span class="save-label" id="note-save-state">Saved automatically</span></div><textarea class="detail-notes" id="job-notes" aria-label="Your notes for this job" placeholder="Add notes…">${esc(r.notes)}</textarea></section>
    <section class="detail-section"><h3>Comments <span class="optional">${job.comments.length}</span></h3><div id="comments">${job.comments.map(c=>`<div class="comment"><div class="comment-meta"><strong>${esc(state.profile.profile.name||'You')}</strong><span>${shortDate(c.created_at)}</span><button class="text-button" data-delete-comment="${esc(c.id)}" aria-label="Delete comment">Delete</button></div><div class="comment-body">${esc(c.body)}</div></div>`).join('')}</div><form id="comment-form" class="comment-compose"><textarea id="comment-input" aria-label="Write a comment" placeholder="Write a comment…" maxlength="10000" required></textarea><div class="comment-compose-footer"><button class="button small" type="submit">Add comment</button></div></form></section>
    <section class="detail-section"><h3>Job description</h3><div class="description ${!job.description?'empty':''}">${esc(job.description||'No description saved. Open the listing to read more.')}</div></section>
    ${job.sources.length?`<section class="detail-section"><h3>Sources</h3>${job.sources.map(s=>`<div class="source-evidence"><a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a>${s.evidence?`<p>${esc(s.evidence)}</p>`:''}<div>Last checked ${shortDate(s.retrieved_at)}</div></div>`).join('')}</section>`:''}
    <div class="detail-bottom"><span>Added ${shortDate(job.first_seen_at)}</span><span>${r.reviewed_at?'Reviewed '+shortDate(r.reviewed_at):'Not reviewed'}</span></div></div>`;
  $('#close-job').onclick=()=>closeJob().catch(report);
  $('#detail-status').onchange=e=>saveReview(job.id,{status:e.target.value},true).catch(report);
  $('#mark-reviewed').onclick=()=>saveReview(job.id,{reviewed:!state.detail.review.reviewed_at},true).catch(report);
  $('#job-notes').oninput=e=>{pendingNote={id:job.id,notes:e.target.value};$('#note-save-state').textContent='Unsaved changes';clearTimeout(noteTimer);noteTimer=setTimeout(()=>flushNotes().catch(report),650);};
  $('#comment-form').onsubmit=async e=>{e.preventDefault();const input=$('#comment-input'),body=input.value.trim();if(!body)return;const button=$('button',e.target);button.disabled=true;try{await flushNotes();state.detail=await api(`/api/jobs/${job.id}/comments`,{method:'POST',body:JSON.stringify({body})});await refresh();renderDetail();toast('Comment saved');}catch(err){button.disabled=false;report(err);}};
  $$('[data-delete-comment]').forEach(el=>el.onclick=async()=>{try{await flushNotes();state.detail=await api(`/api/comments/${el.dataset.deleteComment}`,{method:'DELETE'});await refresh();renderDetail();toast('Comment deleted');}catch(err){report(err);}});
}
function flushNotes(){
  clearTimeout(noteTimer);const note=pendingNote;pendingNote=null;
  if(!note)return noteChain;
  const promise=noteChain.catch(()=>{}).then(async()=>{
    if($('#note-save-state'))$('#note-save-state').textContent='Saving…';
    try{const result=await api(`/api/jobs/${note.id}/review`,{method:'PATCH',body:JSON.stringify({notes:note.notes})});if(state.detail?.id===note.id)state.detail=result;await refresh();if($('#note-save-state'))$('#note-save-state').textContent='All changes saved';}
    catch(error){if(!pendingNote)pendingNote=note;if($('#note-save-state'))$('#note-save-state').textContent='Could not save · retry by editing';throw error;}
  });noteChain=promise;return promise;
}
async function saveReview(id,patch,redraw=false){await flushNotes();const detail=await api(`/api/jobs/${id}/review`,{method:'PATCH',body:JSON.stringify(patch)});if(state.detail?.id===id)state.detail=detail;await refresh();if(redraw)renderDetail();toast(patch.status?'Moved to '+statuses[patch.status]:patch.reviewed===false?'Marked as not reviewed':'Review saved');}
async function closeJob(){await flushNotes();$('#job-dialog').close();state.detail=null;focusBeforeDialog?.focus();}
function openAdd(status='new'){$('#add-form').reset();$('#add-status').innerHTML=Object.entries(statuses).map(([k,v])=>`<option value="${k}" ${k===status?'selected':''}>${v}</option>`).join('');$('#add-dialog').showModal();}
function openSearch(){$('#search-profile-hint').textContent=profileReady()?`Your saved search profile (version ${state.profile.version}) will guide the next search.`:'Your profile is empty. Add your skills and preferences to guide the search.';$('#search-dialog').showModal();}
function inputField(label,name,options={}){const p=state.profileDraft;const value=options.array?(p[name]||[]).join(', '):p[name]??'';return `<label>${label}<input name="${name}" type="${options.type||'text'}" value="${esc(value)}" ${options.type==='number'?'min="0" step="any"':''} ${options.placeholder?`placeholder="${esc(options.placeholder)}"`:''}>${options.hint?`<span class="field-hint">${options.hint}</span>`:''}</label>`;}
function textField(label,name,placeholder='',rows=3){return `<label>${label}<textarea name="${name}" rows="${rows}" placeholder="${esc(placeholder)}">${esc(state.profileDraft[name]||'')}</textarea></label>`;}
function renderProfile(){
  if(!state.profileDraft){state.profileDraft=structuredClone(state.profile.profile);state.profileDraftVersion=state.profile.version;}
  const p=state.profileDraft;
  $('#main').innerHTML=`<section class="page profile-page">${heading('Your search profile','Your background and preferences for future searches.',`<span class="profile-save-state" id="profile-save-state">${state.profile.updated_at?'Saved '+shortDate(state.profile.updated_at):'Not saved yet'}</span><button class="button primary" id="save-profile">Save profile</button>`)}<div class="profile-intro"><span>Claude uses your saved profile when you start a new search.</span></div><form id="profile-form"><section class="profile-section"><h2 class="section-heading">About you</h2><p class="section-copy">Your experience and the work you want to do next.</p><div class="form-grid">${inputField('Your name','name',{placeholder:'Your name'})}${inputField('Professional headline','headline',{placeholder:'e.g. Senior product designer'})}</div>${textField('Your background','about','What have you worked on? What would you love to do next?',4)}<div class="form-grid">${inputField('Years of experience','experience_years',{type:'number',placeholder:'e.g. 5'})}${inputField('Portfolio or personal website','portfolio_url',{type:'url',placeholder:'https://…'})}</div></section><section class="profile-section"><h2 class="section-heading">Roles & skills</h2><p class="section-copy">Use commas to separate roles, skills, and other lists. Related titles help broaden the search.</p>${inputField('Target roles','roles',{array:true,placeholder:'Product designer, UX designer, design engineer'})}${inputField('Skills & strengths','skills',{array:true,placeholder:'User research, Figma, prototyping, product strategy'})}<div class="form-grid">${inputField('Seniority','seniority',{array:true,placeholder:'Mid-level, senior, lead'})}${inputField('Employment types','employment_types',{array:true,placeholder:'Full-time, contract, part-time'})}</div></section><section class="profile-section"><h2 class="section-heading">Location & eligibility</h2><p class="section-copy">Remote can still have location restrictions. These details help Claude check eligibility.</p><div class="form-grid">${inputField('Where you’re based','location',{placeholder:'City, country'})}${inputField('Your timezone','timezone',{placeholder:'e.g. Asia/Tbilisi'})}</div>${inputField('Locations or regions to search','target_locations',{array:true,placeholder:'Worldwide, Europe, EMEA',hint:'A search preference, not proof that you’re eligible to work there.'})}<div class="form-grid">${inputField('Work authorization','work_authorization',{placeholder:'Countries or arrangements you’re eligible for'})}${inputField('Working languages','languages',{array:true,placeholder:'English, Georgian'})}</div><label class="checkbox-label"><input type="checkbox" name="fully_remote_required" ${p.fully_remote_required?'checked':''}><span><strong>Fully remote is a requirement</strong><small>Otherwise, remote remains a preference and uncertain listings stay visible.</small></span></label></section><section class="profile-section"><h2 class="section-heading">Compensation</h2><p class="section-copy">Keep the amount and period together. Listings without salary information stay available for review.</p><div class="form-grid three">${inputField('Minimum amount','salary_min',{type:'number',placeholder:'e.g. 60000'})}${inputField('Currency','salary_currency',{placeholder:'USD'})}<label>Per<select name="salary_period">${['year','month','hour'].map(v=>`<option value="${v}" ${p.salary_period===v?'selected':''}>${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}</select></label></div><label class="checkbox-label"><input type="checkbox" name="salary_is_requirement" ${p.salary_is_requirement?'checked':''}><span><strong>This minimum is a requirement</strong><small>Otherwise, use it as a preference when evaluating fit.</small></span></label></section><section class="profile-section"><h2 class="section-heading">Preferences</h2><p class="section-copy">Add any requirements or preferences for the next search.</p>${textField('Must-haves','must_haves','e.g. Async culture, a hands-on role, at least four hours of timezone overlap')}${textField('Things to avoid','avoid','e.g. Commission-only roles, frequent travel, agencies')}${textField('Anything else Claude should know','extra_context','Career direction, industries you’re curious about, or context for the next search.')}</section><div class="profile-form-bottom"><span>Changes apply to your next search.</span><button class="button primary" type="submit">Save profile </button></div></form></section>`;
  const form=$('#profile-form');
  form.oninput=()=>{readProfileForm();$('#profile-save-state').textContent='Unsaved changes';};
  form.onsubmit=saveProfile;$('#save-profile').onclick=()=>form.requestSubmit();
}
function readProfileForm(){const form=$('#profile-form');if(!form)return;const data=new FormData(form),p={};for(const [key,value]of data)p[key]=value.trim();for(const k of ['roles','skills','seniority','languages','target_locations','employment_types'])p[k]=(p[k]||'').split(',').map(v=>v.trim()).filter(Boolean);for(const k of ['salary_min','experience_years'])p[k]=p[k]===''?null:Number(p[k]);for(const k of ['fully_remote_required','salary_is_requirement'])p[k]=form.elements[k].checked;p.salary_currency=p.salary_currency.toUpperCase();state.profileDraft=p;}
async function saveProfile(e){e.preventDefault();readProfileForm();const button=$('#save-profile');button.disabled=true;try{state.profile=await api('/api/profile',{method:'PUT',body:JSON.stringify({profile:state.profileDraft,expected_version:state.profileDraftVersion})});state.profileDraft=null;await refresh();toast('Profile saved. Your next search will use it.');}catch(error){button.disabled=false;report(error);}}
function renderSources(){
  $('#main').innerHTML=`<section class="page sources-page">${heading('Sources & searches','Choose where Claude looks for jobs.',`<button class="button" id="source-search">Search with Claude</button>`)}<h2 class="section-heading">Sources <span class="optional">${state.sources.filter(s=>s.enabled).length} enabled</span></h2><div class="source-list">${state.sources.map(s=>`<div class="source-row"><div><a href="${esc(s.url)}" class="source-name" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a><div class="source-domain">${esc(new URL(s.url).hostname)} · ${s.last_successful_at?'Last searched '+shortDate(s.last_successful_at):'Not searched yet'}</div></div><span class="source-method">${s.method==='discover'?'Needs setup':s.method==='manual'?'Alerts / manual':s.method.toUpperCase()}</span><input type="checkbox" data-source="${esc(s.id)}" aria-label="Enable ${esc(s.name)}" ${s.enabled?'checked':''}></div>`).join('')}</div><h2 class="section-heading">Search history</h2><p class="section-copy">Each search uses a snapshot of your saved profile.</p>${state.runs.length?state.runs.map(run=>`<div class="run-row"><div><p>${esc(run.request)}</p><small>${shortDate(run.started_at)} · ${run.profile_snapshot?'Profile v'+run.profile_snapshot.version:'No profile snapshot'}${run.notes?' · '+esc(run.notes):''}</small></div><span class="run-badge ${esc(run.status)}">${esc(run.status)}</span></div>`).join(''):`<p class="empty-searches">No searches yet.</p>`}</section>`;
  $('#source-search').onclick=openSearch;
  $$('[data-source]').forEach(el=>el.onchange=async()=>{try{await api(`/api/sources/${el.dataset.source}`,{method:'PATCH',body:JSON.stringify({enabled:el.checked})});await refresh();toast('Source updated');}catch(error){el.checked=!el.checked;report(error);}});
}
$$('[data-view]').forEach(el=>el.onclick=()=>navigate(el.dataset.view));
$('.workspace-brand').onclick=e=>{e.preventDefault();navigate('board');};
$('#refresh-button').onclick=()=>refresh().then(()=>toast('Workspace refreshed')).catch(report);
$('#menu-button').onclick=()=>{const open=$('#sidebar').classList.toggle('mobile-open');$('#menu-button').setAttribute('aria-expanded',String(open));};
$$('[data-close]').forEach(el=>el.onclick=()=>$('#'+el.dataset.close).close());
$('#job-dialog').addEventListener('cancel',e=>{e.preventDefault();closeJob().catch(report);});
$('#job-dialog').addEventListener('click',e=>{if(e.target===$('#job-dialog')&&e.clientX<$('#job-dialog').getBoundingClientRect().left)closeJob().catch(report);});
$('#search-edit-profile').onclick=()=>{$('#search-dialog').close();navigate('profile');};
$('#copy-search').onclick=async()=>{try{await navigator.clipboard.writeText($('#search-command').textContent);toast('Command copied. Paste it in Claude Code.');}catch{toast('Select the command above and copy it.',true);}};
$('#add-form').onsubmit=async e=>{e.preventDefault();const form=e.target,values=Object.fromEntries(new FormData(form)),status=values.status;delete values.status;const button=$('[type="submit"]',form);button.disabled=true;try{const job=await api('/api/jobs',{method:'POST',body:JSON.stringify({job:values,status})});$('#add-dialog').close();await refresh();await openJob(job.id);toast('Opportunity added');}catch(error){report(error);}finally{button.disabled=false;}};
window.addEventListener('beforeunload',e=>{if(pendingNote){e.preventDefault();e.returnValue='';}});
refresh().catch(error=>{$('#main').innerHTML=`<div class="error-page"><h2>Couldn’t open your workspace</h2><p>${esc(error.message)}</p><button class="button" id="retry-load">Try again</button></div>`;$('#retry-load').onclick=()=>location.reload();});
