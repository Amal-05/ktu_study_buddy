import {useEffect,useState} from 'react'
import {Search,Target} from 'lucide-react'
import {api,type Source,type Upload} from './api'
import {Busy,Empty,ErrorBox,Heading,Markdown,Sources,type Shared,useAction} from './ui'
export type Question={id:string,text:string,marks:number,module:string,topic:string,generated:boolean}
export type Paper={total:number,minutes:number,sections:{name:string,marks:number,max_subdivisions:number,groups:{module:string,name:string,attempt:number,questions:Question[]}[]}[]}
type Evidence={text:string,name:string,year?:number,scheme?:number,url:string,page:number,session:string,marks?:number}
type Prediction={module:string,topic:string,score:number,priority:string,factors:Record<string,number>,evidence:Evidence[],clusters:Evidence[][]}
type Data={confidence:string,matched_questions:number,scheme_papers:number,topics:Prediction[],weights:Record<string,number>,backtest?:{year:number,session:string,hits:number,total:number,rate:number,method:string}}
type Answer={text:string,sources:Source[],provider:string}
type Candidate={url:string,name:string,text:string}

export function Exams(shared:Shared&{practice:()=>void}){
  const [id,setId]=useState(shared.selected??shared.subjects[0]?.id??0)
  const [data,setData]=useState<Data>()
  const [paper,setPaper]=useState<Paper>()
  const [files,setFiles]=useState<Upload[]>([])
  const [candidates,setCandidates]=useState<Candidate[]|null>(null)
  const [answer,setAnswer]=useState<{id:string,value:Answer}>()
  const [showPaper,setShowPaper]=useState(false)
  const action=useAction()
  const base='/subjects/'+id
  const subject=shared.subjects.find(row=>row.id===id)
  async function load(){
    const [summary,uploads]=await Promise.all([api<Data>(base+'/predictions'),api<Upload[]>(base+'/uploads')]);setData(summary);setFiles(uploads.filter(row=>row.tag==='pyq'))
    if(subject?.outline.modules.length)setPaper(await api<Paper>(base+'/paper'))
  }
  useEffect(()=>{if(!id&&shared.subjects[0])setId(shared.subjects[0].id)},[shared.subjects.length])
  useEffect(()=>{setData(undefined);setPaper(undefined);setCandidates(null);setAnswer(undefined);setShowPaper(false);if(id)void action.run(load)},[id])
  function model(q:Question){void action.run(async()=>setAnswer({id:q.id,value:await api(base+'/model-answer',{module:q.module,topic:q.topic,question:q.text,marks:q.marks})}))}
  return <>
    <Heading title="Exam prep" action={<button className="btn primary" disabled={!paper} onClick={shared.practice}><Target size={17}/>Timed practice</button>}>Rank likely topics, inspect evidence, and build a paper from your confirmed pattern.</Heading>
    <ErrorBox text={action.error}/>
    <section className="card panel">
      <div className="inline-form"><label>Subject<select value={id} onChange={e=>setId(Number(e.target.value))}><option value={0}>Select subject</option>{shared.subjects.map(row=><option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}</select></label>
      <button className="btn secondary" disabled={!id||action.busy} onClick={()=>void action.run(async()=>setCandidates(await api(base+'/paper-search',{})))}><Search size={16}/>Find past papers</button>
      <button className="btn secondary" disabled={!paper||action.busy} onClick={()=>setShowPaper(!showPaper)}>{showPaper?'Close mock paper':'Preview mock paper'}</button></div>
      {action.busy&&<Busy/>}
      {candidates&&<div className="notice"><h3>Web results for approval</h3>{candidates.length?candidates.map(row=><div className="subject-row" key={row.url}><div><a href={row.url} target="_blank" rel="noreferrer">{row.name}</a><small>{row.text.slice(0,180)}</small></div><button className="btn secondary" disabled={action.busy} onClick={()=>{if(confirm('Approve this public PDF for use in predictions?'))void action.run(async()=>{await api(base+'/papers/approve',{url:row.url,approved:true});await load();setCandidates(candidates.filter(item=>item.url!==row.url))})}}>Approve PDF</button></div>):<p>No verified public results. Search mock mode returns no web papers.</p>}</div>}
      {files.length>0&&<div className="subject-list">{files.map(row=><PaperMetadata key={row.id} row={row} base={base} refresh={load}/>)}</div>}
    </section>
    {data&&<section className="card panel">
      <div className="section-head"><h2>Topic priorities</h2><span className="chip">{data.matched_questions} matched questions</span></div>
      <p className="notice">{data.confidence}. Predictions are estimates. Generated questions are labelled.</p>
      {data.backtest&&<p>Backtest · held out {data.backtest.session} {data.backtest.year}: {data.backtest.hits}/{data.backtest.total} topics matched ({Math.round(data.backtest.rate*100)}%). {data.backtest.method}.</p>}
      <details><summary>Scoring weights</summary><dl className="factors">{Object.entries(data.weights).map(([key,value])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{value}</dd></div>)}</dl></details>
      {!data.topics.length&&<Empty title="No topics ranked">Confirm a syllabus in the subject library.</Empty>}
      {subject?.outline.modules.map(module=><div className="coverage-group" key={module.id}><h3>{module.name}</h3>
        {data.topics.filter(topic=>topic.module===module.id).map(topic=><details key={topic.topic}><summary>{topic.topic} <span className="chip">{topic.priority} · {topic.score}</span></summary>
          <dl className="factors">{Object.entries(topic.factors).map(([key,value])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{value}</dd></div>)}</dl>
          {topic.evidence.length?<ul>{topic.evidence.map((ev,i)=><li key={i}><a href={ev.url} target="_blank" rel="noreferrer">{ev.name} · p. {ev.page}</a> · {ev.year??'Year unknown'} {ev.session} · {ev.scheme===2024?'2024 scheme':ev.scheme?'Older scheme '+ev.scheme:'Scheme unknown'}{ev.marks?' · '+ev.marks+' marks':''}<p>{ev.text}</p></li>)}</ul>:<p className="muted">Syllabus factors only. No matched past-paper evidence.</p>}
          {topic.clusters.some(group=>group.length>1)&&<p>{topic.clusters.filter(group=>group.length>1).length} repeated-question clusters</p>}
        </details>)}
        {paper?.sections.map(section=><div className="prediction-questions" key={section.name}><h4>Part {section.name} · {section.marks} marks</h4>{section.groups.find(group=>group.module===module.id)?.questions.map(q=><div className="question-row" key={q.id}><span><span className="chip">Generated</span> {q.text}</span><button className="btn secondary" disabled={action.busy} onClick={()=>model(q)}>Model answer</button>
          {answer?.id===q.id&&<div className="wide"><Markdown value={answer.value.text} sources={answer.value.sources}/><Sources sources={answer.value.sources}/><small>{answer.value.provider}</small></div>}</div>)}</div>)}
      </div>)}
    </section>}
    {showPaper&&paper&&<section className="card panel"><div className="section-head"><h2>Generated mock paper</h2><span className="chip">{paper.total} marks · {paper.minutes} minutes</span></div>{paper.sections.map(section=><div key={section.name}><h3>Part {section.name}</h3>{section.groups.map(group=><div key={group.module}><h4>{group.name} · Attempt {group.attempt} of {group.questions.length}</h4><ol>{group.questions.map(q=><li key={q.id} value={Number(q.id)}>{q.text} ({q.marks} marks)</li>)}</ol></div>)}</div>)}</section>}
  </>
}
function PaperMetadata({row,base,refresh}:{row:Upload,base:string,refresh:()=>Promise<void>}){
  const [year,setYear]=useState(row.metadata.year??0),[scheme,setScheme]=useState(row.metadata.scheme??0),[session,setSession]=useState(row.metadata.session??'')
  const [questions,setQuestions]=useState<Evidence[]>([])
  const action=useAction()
  return <details><summary>{row.name}</summary><form className="inline-form" onSubmit={e=>{e.preventDefault();void action.run(async()=>{await api(base+'/papers/'+row.id,{year:year||null,scheme:scheme||null,session},'PUT');await refresh()})}}>
    <label>Year<input type="number" placeholder="Unknown" value={year||''} onChange={e=>setYear(Number(e.target.value))}/></label><label>Scheme<input type="number" placeholder="Unknown" value={scheme||''} onChange={e=>setScheme(Number(e.target.value))}/></label><label>Session<input value={session} onChange={e=>setSession(e.target.value)}/></label>
    <button className="btn secondary" disabled={action.busy}>Save metadata</button><button className="btn plain" type="button" disabled={action.busy} onClick={()=>void action.run(async()=>{const got=await api<{metadata:{year?:number,scheme?:number,session?:string},questions:Evidence[]}>(base+'/papers/'+row.id+'/questions');setQuestions(got.questions);setYear(got.metadata.year??0);setScheme(got.metadata.scheme??0);setSession(got.metadata.session??'')})}>Read detected metadata</button>
  </form><ErrorBox text={action.error}/>{questions.length>0&&<ol>{questions.map((item,index)=><li key={index}>{item.text} ({item.marks??'Unknown'} marks)</li>)}</ol>}</details>
}
