import {useEffect,useState} from 'react'
import {BookOpen,CalendarDays,ClipboardList,Clock3,FileText,Plus,Sparkles,Target} from 'lucide-react'
import {api,localDay,type Proposal,type Subject} from './api'
import {Empty,ErrorBox,Heading,Markdown,useAction} from './ui'
type Task={id:string,name:string,topic:string,minutes:number,done:boolean}
type Data={subjects:{id:number,name:string,code:string,days:number,coverage:number,mastery:number,weak:{topic:string,value:number,attempts:number,module:string}[]}[],plan:{tasks:Task[],hours:number},missed:number}
const pct=(n:number)=>Math.round(n*100)
export function Dashboard({planner,revision,subjects,add,demo,choose,go,proposal,clearProposal}:{planner:boolean,revision:number,subjects:Subject[],add:()=>void,demo:()=>void,choose:(id:number)=>void,go:(page:string)=>void,proposal?:Proposal,clearProposal:()=>void}){
  const [info,setInfo]=useState<Data>()
  const [hours,setHours]=useState(2)
  const [sheet,setSheet]=useState('')
  const action=useAction(), day=localDay()
  async function load(){const value=await api<Data>('/dashboard?day='+day);setInfo(value);setHours(value.plan.hours)}
  useEffect(()=>{void action.run(load)},[revision])
  const list=info?.subjects??[],next=list.find(item=>item.days>=0)
  const tasks=info?.plan.tasks??[]
  const average=(key:'coverage'|'mastery')=>pct(list.reduce((total,row)=>total+row[key],0)/Math.max(1,list.length))+'%'
  function plan(){void action.run(async()=>{await api('/plan',{hours,day});await load();clearProposal()})}
  function tick(task:Task){void action.run(async()=>{await api('/plan',{task:task.id,done:!task.done,day},'PATCH');await load()})}
  const stats=[{Icon:BookOpen,title:'Subjects',value:subjects.length,caption:'In your library',color:'violet'},{Icon:FileText,title:'Syllabus coverage',value:average('coverage'),caption:'Topics with study material',color:'blue'},{Icon:Target,title:'Average mastery',value:average('mastery'),caption:'Based on practice results',color:'mint'},{Icon:Clock3,title:'Next exam',value:next?next.days+' days':'—',caption:next?.code??'Set an exam date',color:'amber'}]
  const weak=list.flatMap(row=>row.weak.filter(topic=>topic.attempts).map(topic=>({...topic,code:row.code}))).sort((a,b)=>a.value-b.value).slice(0,7)
  return <>
    <Heading title={planner?'Study planner':'Study overview'} action={<button className="btn primary" onClick={planner?plan:add} disabled={action.busy}>{planner?<CalendarDays size={17}/>:<Plus size={18}/>} {planner?'Build today’s plan':'Add subject'}</button>}>{planner?'Plan around your exams and the topics that need practice.':'Track your subjects, build confidence, and prepare for your next exam.'}</Heading>
    <ErrorBox text={action.error}/>
    {!planner&&<>
      <section className="welcome">
        <div>
        <h2>{subjects.length?'Make progress today':'Ready to start studying?'}</h2>
        <p>{subjects.length?'Review your coverage, revisit a weak topic, or work through today’s plan.':'Add a subject and upload its syllabus to organize your materials, generate notes, and start practicing.'}</p>
        <div className="actions"><button className="btn hero-primary" onClick={subjects.length?()=>go('planner'):add}>{subjects.length?'Open study planner':'Add your first subject'}</button>{!subjects.length&&<button className="btn hero-secondary" onClick={demo}><BookOpen size={16}/>Load sample subject</button>}</div></div>
        <div className="welcome-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="art-card back-card"/><div className="art-card front-card"><BookOpen size={72} strokeWidth={1.15}/><span/><span/></div><Sparkles className="art-sparkle" size={30}/></div>
      </section>
      <div className="stats-grid">{stats.map(({Icon,title,value,caption,color})=><article className="stat card" key={title}><div className={'stat-icon '+color}><Icon size={19}/></div><div><p>{title}</p><strong>{value}</strong><small>{caption}</small></div></article>)}</div>
    </>}
    <div className="dashboard-columns">
      <section className="card panel"><div className="section-head"><h2><ClipboardList size={20}/>Today’s plan</h2><span className="muted">{tasks.filter(task=>task.done).length}/{tasks.length} complete</span></div>
        {planner&&<div className="inline-form"><label>Available hours<input type="number" min=".25" max="12" step=".25" value={hours} onChange={e=>setHours(Number(e.target.value))}/></label><button className="btn secondary" disabled={action.busy} onClick={plan}>Re-plan today</button></div>}
        {proposal?.tool==='plan'&&<p className="notice">Choose your hours and build the plan to apply the chat request.</p>}
        {!!info?.missed&&<p className="notice">{info.missed} unfinished tasks from prior plans.</p>}
        {tasks.length?<ul className="task-list">{tasks.map(task=><li key={task.id}><label><input type="checkbox" checked={task.done} disabled={action.busy} onChange={()=>tick(task)}/><span>{task.topic}<small>{task.name} · {task.minutes} min</small></span></label></li>)}</ul>:<Empty title="No tasks planned" actions={<button className="btn secondary" onClick={planner?plan:()=>go('planner')}>{planner?'Build plan':'Open planner'}</button>}>Add an upcoming exam and confirm a syllabus to build a daily plan.</Empty>}
      </section>
      <section className="card panel"><div className="section-head"><h2><Target size={20}/>Topics to revisit</h2></div>
        {weak.length?<div className="weak">{weak.map(topic=><div key={topic.code+topic.module+topic.topic}><span>{topic.topic}<small>{topic.code} · Module {topic.module}</small></span><strong>{pct(topic.value)}%</strong></div>)}</div>:<Empty title="Mastery starts with practice">Complete a lesson question, flashcard review, or graded answer to see topics here.</Empty>}
      </section>
    </div>
    {subjects.length>0&&<section className="panel card">
      <div className="section-head"><h2><BookOpen size={20}/>Your subjects</h2><button className="btn plain" onClick={()=>go('library')}>View library</button></div>
      <div className="subject-list">{list.map(row=><div className="subject-row" key={row.id}>
        <button className="subject-link" onClick={()=>choose(row.id)}><span className="stat-icon violet"><BookOpen size={17}/></span><span><strong>{row.name}</strong><small>{row.code} · {row.days>=0?row.days+' days to exam':'Exam passed'}</small></span></button>
        <button className="btn secondary" disabled={action.busy} onClick={()=>void action.run(async()=>{setSheet((await api<{text:string}>('/subjects/'+row.id+'/revision')).text)})}>Revision sheet</button>
      </div>)}</div>
      {sheet&&<div className="revision"><div className="section-head"><h3>Last-day revision</h3><button className="btn plain" onClick={()=>setSheet('')}>Close</button></div><Markdown value={sheet}/></div>}
    </section>}
  </>
}
