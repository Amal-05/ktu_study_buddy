import {useEffect,useState} from 'react'
import {MessageSquare,Sparkles} from 'lucide-react'
import {api,type Scope,type Source} from './api'
import {Busy,Empty,ErrorBox,Heading,Markdown,ScopeFields,Sources,defaultScope,type Shared,useAction} from './ui'
export {Exams} from './exams'
export {Practice} from './practice'

export function useStudy(shared:Shared){
  const [scope,setScope]=useState(shared.scope??defaultScope(shared.subjects,shared.selected))
  useEffect(()=>{if(!scope.subject_id&&shared.subjects.length)setScope(shared.scope??defaultScope(shared.subjects,shared.selected))},[shared.subjects.length])
  const base=scope.subject_id?'/subjects/'+scope.subject_id:''
  const params=new URLSearchParams({module:scope.module??'',topic:scope.topic??''}).toString()
  return {scope,setScope,base,params,valid:!!(scope.subject_id&&scope.module&&scope.topic)}
}
type Version={id:string,text:string,created:string,sources:Source[],edited:boolean,provider:string,estimated_tokens:number}
type Note={versions:Version[],active:string|null}
type Job={id:string,state:string,topics:string[],completed:number,message:string,retry_at:number}
export function Notes(shared:Shared&{refresh:()=>Promise<void>}){
  const study=useStudy(shared)
  const {scope,base,valid,params}=study
  const action=useAction()
  const [note,setNote]=useState<Note>()
  const [version,setVersion]=useState('')
  const [edit,setEdit]=useState<string>()
  const [job,setJob]=useState<Job>()
  async function load(){if(valid){const value=await api<Note>(base+'/notes?'+params);setNote(value);setVersion(value.active??'')}}
  useEffect(()=>{setNote(undefined);setEdit(undefined);setVersion('');void action.run(load)},[base,params])
  useEffect(()=>{
    let active=true
    setJob(undefined)
    if(base)void api<Job|null>(base+'/notes/jobs').then(value=>{if(active&&value)setJob(value)}).catch(error=>{if(active)action.setError(String(error))})
    return()=>{active=false}
  },[base])
  useEffect(()=>{
    if(!job||!['queued','running','waiting'].includes(job.state))return
    let active=true
    let timer:number
    async function poll(){
      try{const next=await api<Job>('/jobs/'+job!.id);if(!active)return;setJob(next)
        if(next.state==='done'){await load();await shared.refresh()}
      } catch(e){if(active)action.setError(String(e))}
    }
    timer=window.setTimeout(()=>void poll(),850)
    return()=>{active=false;clearTimeout(timer)}
  },[job,base,params])
  function generate(module:boolean){void action.run(async()=>{setJob(await api(base+'/notes/jobs',{module:scope.module,topic:module?null:scope.topic}));shared.clearProposal()})}
  const selected=note?.versions.find(item=>item.id===version)
  const inProgress=job&&['queued','running','waiting'].includes(job.state)
  return <>
    <Heading title="Study notes">Generate cited notes by topic or module, then keep your own revisions.</Heading>
    <ErrorBox text={action.error}/>
    <section className="panel card">
      <ScopeFields subjects={shared.subjects} scope={scope} onChange={study.setScope} requireTopic/>
      {shared.proposal?.tool==='generate_notes'&&<p className="notice">Confirm the scope and generate notes to apply the chat request.</p>}
      <div className="actions"><button className="btn primary" disabled={!valid||!!inProgress||action.busy} onClick={()=>generate(false)}><Sparkles size={16}/>{selected?'Regenerate topic':'Generate topic notes'}</button>
      <button className="btn secondary" disabled={!scope.module||!!inProgress||action.busy} onClick={()=>generate(true)}>Generate module notes</button></div>
      {job&&<div className="notice" role="status"><strong>{job.state} · {job.completed}/{job.topics.length} topics</strong><progress max={job.topics.length} value={job.completed}/>{job.message&&<p>{job.message}</p>}{['failed','waiting'].includes(job.state)&&<button className="btn secondary" onClick={()=>void action.run(async()=>setJob(await api('/jobs/'+job.id+'/retry',{})))}>Retry</button>}</div>}
    </section>
    <section className="panel card">
      {selected?<><div className="section-head"><h2>{scope.topic}</h2><span className="chip">{selected.provider} · ~{selected.estimated_tokens} tokens</span></div>
        <div className="actions"><label>Revision<select value={version} onChange={e=>{setVersion(e.target.value);setEdit(undefined)}}>{note!.versions.map(item=><option key={item.id} value={item.id}>{item.edited?'Student edit':'Generated'} · {new Date(item.created).toLocaleString()}</option>)}</select></label>
          <button className="btn secondary" onClick={()=>setEdit(selected.text)}>Edit Markdown</button>
          <a className="btn secondary" href={'/api'+base+'/notes/export?'+params+'&version='+version+'&format=md'}>Markdown</a>
          <a className="btn secondary" href={'/api'+base+'/notes/export?'+params+'&version='+version+'&format=pdf'}>PDF</a></div>
        {edit===undefined?<Markdown value={selected.text} sources={selected.sources}/>:<form onSubmit={e=>{e.preventDefault();void action.run(async()=>{const value=await api<Note>(base+'/notes',{module:scope.module,topic:scope.topic,version,text:edit},'PUT');setNote(value);setVersion(value.active!);setEdit(undefined)})}}>
          <label>Markdown<textarea rows={18} value={edit} onChange={e=>setEdit(e.target.value)}/></label><div className="actions"><button className="btn primary" disabled={action.busy}>Save revision</button><button className="btn plain" type="button" onClick={()=>setEdit(undefined)}>Cancel</button></div></form>}
        <Sources sources={selected.sources}/>
      </>:<Empty title="No notes selected">Choose a confirmed module and topic to generate your first set of notes.</Empty>}
    </section>
  </>
}

type Lesson={step:number,explanation:string,example:string,question:string,answer?:string,sources:Source[],provider:string,evaluation?:{correct:boolean,feedback:string}}
const controls=[['simpler','Simpler'],['more detail','More detail'],['another example','Another example'],['Malayalam-English','Malayalam-English mix'],['quiz me','Quiz me']]
export function Teacher(shared:Shared&{chat:(scope:Scope)=>void}){
  const {scope,setScope,base,params,valid}=useStudy(shared)
  const action=useAction()
  const [lesson,setLesson]=useState<Lesson>()
  const [answer,setAnswer]=useState('')
  useEffect(()=>{setLesson(undefined);setAnswer('');if(valid)void action.run(async()=>{const value=await api<Lesson>(base+'/lesson?'+params);if(value.step){setLesson(value);setAnswer(value.answer??'')}})},[base,params])
  function request(mode:string){void action.run(async()=>{const value=await api<Lesson>(base+'/lesson',{module:scope.module,topic:scope.topic,action:mode,answer:mode==='answer'?answer:''});setLesson(value);if(mode!=='answer')setAnswer('')})}
  return <>
    <Heading title="Teacher mode">Take one step at a time, answer a check question, and resume later.</Heading>
    <ErrorBox text={action.error}/>
    <section className="card panel"><ScopeFields subjects={shared.subjects} scope={scope} onChange={setScope} requireTopic/>
      <div className="actions"><button className="btn primary" disabled={!valid||action.busy} onClick={()=>request('start')}>{lesson?'Resume lesson':'Start lesson'}</button><button className="btn secondary" disabled={!valid} onClick={()=>shared.chat(scope)}><MessageSquare size={16}/>Ask a doubt</button></div></section>
    <section className="card panel">{action.busy&&<Busy/>}{lesson?<><div className="section-head"><h2>{scope.topic} · Step {lesson.step}</h2><span className="chip">{lesson.provider}</span></div>
      <Markdown value={lesson.explanation} sources={lesson.sources}/>
      {lesson.example&&<div className="notice"><h3>Worked example</h3><Markdown value={lesson.example} sources={lesson.sources}/></div>}
      <Sources sources={lesson.sources}/>
      <div className="actions">{controls.map(([mode,label])=><button className="btn secondary" disabled={action.busy} key={mode} onClick={()=>request(mode)}>{label}</button>)}</div>
      <form onSubmit={e=>{e.preventDefault();request('answer')}}><label>{lesson.question}<textarea required rows={3} value={answer} onChange={e=>setAnswer(e.target.value)}/></label>
        <div className="actions"><button className="btn primary" disabled={action.busy||!answer.trim()}>Check answer</button>{lesson.evaluation&&<button type="button" className="btn secondary" disabled={action.busy} onClick={()=>request(lesson.evaluation?.correct?'continue':'simpler')}>{lesson.evaluation.correct?'Continue':'Explain differently'}</button>}</div>
      </form>
      {lesson.evaluation&&<p className={lesson.evaluation.correct?'notice success':'notice'}>{lesson.evaluation.feedback}</p>}
    </>:<Empty title="Select a topic to begin">A lesson includes a worked example and a question before each next step.</Empty>}</section>
  </>
}
