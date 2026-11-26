import {createRoot} from 'react-dom/client'
import {useEffect,useState} from 'react'
import {BookOpen, CalendarDays, ChevronDown, ClipboardCheck, FileText, GraduationCap, LayoutGrid, LibraryBig, Menu, MessageSquare, Moon, Settings, Sun, Target, X} from 'lucide-react'
import {api,type Proposal,type Scope,type Subject} from './api'
import {Busy, ErrorBox, useAction} from './ui'
import {Dashboard} from './overview'
import {Library, ProviderSettings} from './library'
import {Notes, Teacher, Exams, Practice} from './learning'
import {Chat} from './chat'
import './style.css'

const entries=[
  {id:'overview',title:'Overview',icon:LayoutGrid},
  {id:'library',title:'Subject library',icon:LibraryBig},
  {id:'notes',title:'Study notes',icon:FileText},
  {id:'teacher',title:'Teacher mode',icon:GraduationCap},
  {id:'chat',title:'Study chat',icon:MessageSquare},
  {id:'exams',title:'Exam prep',icon:Target},
  {id:'practice',title:'Practice',icon:ClipboardCheck},
  {id:'planner',title:'Study planner',icon:CalendarDays},
  {id:'settings',title:'Settings',icon:Settings},
]
function activePage(){const hash=location.hash.slice(1);return entries.find(entry=>entry.id===hash)?.id??'overview'}
function App(){
  const [page,setPage]=useState(activePage)
  const [subjects,setSubjects]=useState<Subject[]>([])
  const [selected,setSelected]=useState<number|null>(null)
  const [showNew,setShowNew]=useState(false)
  const [menu,setMenu]=useState(false)
  const [theme,setTheme]=useState(localStorage.getItem('buddy-theme')??'light')
  const [status,setStatus]=useState<{mock:boolean,search_mock:boolean}|null>(null)
  const [scope,setScope]=useState<Scope>()
  const [proposal,setProposal]=useState<Proposal>()
  const [revision,setRevision]=useState(0)
  const action=useAction()
  async function refresh(){setSubjects(await api<Subject[]>('/subjects'));setRevision(value=>value+1)}
  function go(next:string){location.hash=next;setPage(next);setMenu(false);window.scrollTo(0,0)}
  useEffect(()=>{const listen=()=>setPage(activePage());window.addEventListener('hashchange',listen);return()=>window.removeEventListener('hashchange',listen)},[])
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('buddy-theme',theme)},[theme])
  useEffect(()=>{void action.run(async()=>{await refresh();setStatus(await api('/status'))})},[])
  function newSubject(){setShowNew(true);go('library')}
  function choose(id:number){setSelected(id);setScope(undefined);go('library')}
  function demo(){void action.run(async()=>{const sample=await api<Subject>('/sample',{});setSelected(sample.id);await refresh()})}
  function openChat(next:Scope){setScope(next);go('chat')}
  function perform(tool:Proposal){
    setSelected(tool.subject_id);setScope({subject_id:tool.subject_id,module:tool.module,topic:tool.topic});setProposal(tool)
    go(tool.tool==='generate_notes'?'notes':tool.tool==='predictions'?'exams':tool.tool==='plan'?'planner':'practice')
  }
  const shared={subjects,selected,scope,proposal,clearProposal:()=>setProposal(undefined)}
  return <div className="app-shell">
    {menu&&<button className="mobile-scrim" aria-label="Close navigation" onClick={()=>setMenu(false)}/>}
    <aside className={menu?'sidebar open':'sidebar'}>
      <button className="brand" onClick={()=>go('overview')}><span className="brand-mark"><BookOpen size={22}/></span><span>KTU Study Buddy<small>B.Tech · 2024 scheme</small></span></button>
      <div className="nav-label">Workspace</div>
      <nav>{entries.slice(0,-1).map(({id,title,icon:Icon})=><a key={id} href={'#'+id} aria-current={page===id?'page':undefined} onClick={e=>{e.preventDefault();setScope(undefined);setProposal(undefined);go(id)}}><Icon size={18}/>{title}</a>)}</nav>
      <div className="sidebar-bottom">
        <a href="#settings" aria-current={page==='settings'?'page':undefined} onClick={e=>{e.preventDefault();go('settings')}}><Settings size={18}/>Settings</a>
        <div className="profile"><span className="avatar">S</span><span>My workspace<small>Local study space</small></span><ChevronDown size={14}/></div>
      </div>
    </aside>
    <div className="app-body">
      <header className="topbar">
        <div className="breadcrumbs"><button className="icon mobile-only" aria-label="Open navigation" onClick={()=>setMenu(!menu)}>{menu?<X/>:<Menu/>}</button><span>Workspace</span><span className="slash">/</span><strong>{entries.find(entry=>entry.id===page)?.title}</strong></div>
        <div className="actions">{status&&<button onClick={()=>go('settings')} className={status.mock?'mode-pill mock':'mode-pill'}><span/>{status.mock?'Mock mode':'Live AI'}</button>}
        <button className="icon" aria-label={theme==='dark'?'Light mode':'Dark mode'} onClick={()=>setTheme(theme==='dark'?'light':'dark')}>{theme==='dark'?<Sun size={19}/>:<Moon size={19}/>}</button></div>
      </header>
      <main className="content">
        <ErrorBox text={action.error}/>
        {action.busy&&<Busy label="Updating workspace…"/>}
        {(page==='overview'||page==='planner')&&<Dashboard planner={page==='planner'} revision={revision} subjects={subjects} add={newSubject} demo={demo} choose={choose} go={go} proposal={proposal} clearProposal={shared.clearProposal}/>}
        {page==='library'&&<Library subjects={subjects} selected={selected} select={setSelected} refresh={refresh} create={showNew} setCreate={setShowNew} demo={demo}/>}
        {page==='notes'&&<Notes {...shared} refresh={refresh}/>}
        {page==='teacher'&&<Teacher {...shared} chat={openChat}/>}
        {page==='chat'&&<Chat subjects={subjects} initial={scope} perform={perform}/>}
        {page==='exams'&&<Exams {...shared} practice={id=>{setSelected(id);setScope(undefined);setProposal({tool:'mock',requires_confirmation:false,subject_id:id,module:null,topic:null,argument:''});go('practice')}}/>}
        {page==='practice'&&<Practice {...shared}/>}
        {page==='settings'&&<ProviderSettings status={status}/>}
      </main>
    </div>
  </div>
}
createRoot(document.getElementById('root')!).render(<App/>)
