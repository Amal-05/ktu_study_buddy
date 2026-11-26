import {useEffect,useState} from 'react'
import {Clock3} from 'lucide-react'
import {api,localDay,type Source,type Upload} from './api'
import {Busy,Empty,ErrorBox,Heading,ScopeFields,Sources,type Shared,useAction} from './ui'
import {useStudy} from './learning'
import type {Paper,Question} from './exams'
type Card={id:string,front:string,back:string,due:string,interval:number}
type Grade={possible:number,awarded:number,sources?:Source[],provider?:string,criteria:{criterion:string,possible:number,awarded:number,feedback:string}[],assessment_available?:boolean}
type Attempt={id:string,deadline:number,paper:Paper,result?:{possible:number,awarded:number,questions:(Grade&{question:Question})[]}}
const tabs=[['quiz','Quiz'],['flashcards','Flashcards'],['grade','Grade answer'],['mock','Timed mock']]

export function Practice(shared:Shared){
  const {scope,setScope,base,valid,params}=useStudy(shared)
  const action=useAction()
  const [tab,setTab]=useState(shared.proposal?.tool==='mock'?'mock':shared.proposal?.tool==='grade'?'grade':'quiz')
  const [quiz,setQuiz]=useState<Card[]>([])
  const [cards,setCards]=useState<Card[]>([])
  const [shown,setShown]=useState<string[]>([])
  const [text,setText]=useState('')
  const [question,setQuestion]=useState(shared.proposal?.argument??'')
  const [marks,setMarks]=useState(3)
  const [photo,setPhoto]=useState<Upload>()
  const [grade,setGrade]=useState<Grade>()
  const [attempt,setAttempt]=useState<Attempt>()
  const [answers,setAnswers]=useState<Record<string,string>>({})
  const [clock,setClock]=useState(Date.now())
  const [auto,setAuto]=useState(false)
  const [quizSources,setQuizSources]=useState<Source[]>([])
  const [quizAnswers,setQuizAnswers]=useState<Record<number,string>>({})
  const [results,setResults]=useState<Record<number,Grade>>({})
  const input={module:scope.module,topic:scope.topic}
  async function readCards(){if(base)setCards(await api(base+'/flashcards'))}
  useEffect(()=>{setShown([]);setQuiz([]);setQuizAnswers({});setQuizSources([]);setResults({});setGrade(undefined);setPhoto(undefined)},[base,params])
  useEffect(()=>{setAttempt(undefined);setAnswers({});setAuto(false);if(base)void action.run(async()=>{const [current,list]=await Promise.all([api<Attempt>(base+'/mock'),api<Card[]>(base+'/flashcards')]);setCards(list);if(current.id){setAttempt(current);try{setAnswers(JSON.parse(localStorage.getItem('mock-'+current.id)??'{}'))}catch{setAnswers({})}}})},[base])
  useEffect(()=>{if(!attempt||attempt.result)return;const timer=window.setInterval(()=>setClock(Date.now()),500);return()=>clearInterval(timer)},[attempt])
  const remaining=attempt?Math.max(0,Math.ceil(attempt.deadline-clock/1000)):0
  async function submit(test:Attempt){
    const result=await api<Attempt['result']>('/mock/'+test.id+'/submit',{answers})
    setAttempt({...test,result});localStorage.removeItem('mock-'+test.id)
  }
  useEffect(()=>{if(tab==='mock'&&attempt&&!attempt.result&&remaining===0&&!auto&&!action.busy){setAuto(true);void action.run(()=>submit(attempt))}},[tab,remaining,attempt,auto,action.busy])
  function changeAnswer(id:string,value:string){
    const next={...answers,[id]:value}
    setAnswers(next)
    if(attempt)localStorage.setItem('mock-'+attempt.id,JSON.stringify(next))
  }
  function quizStart(){void action.run(async()=>{const next=await api<{cards:Card[],sources:Source[]}>(base+'/quiz',input);setQuiz(next.cards);setQuizSources(next.sources);setQuizAnswers({});setResults({});setShown([]);shared.clearProposal()})}
  return <>
    <Heading title="Practice">Review flashcards, grade answers, or sit an exam-pattern mock.</Heading>
    <ErrorBox text={action.error}/>
    <section className="card panel"><ScopeFields subjects={shared.subjects} scope={scope} onChange={setScope} requireTopic/>
      <div className="tabs" role="group" aria-label="Practice activity">{tabs.map(([id,label])=><button className={tab===id?'active':''} key={id} onClick={()=>setTab(id)}>{label}</button>)}</div>
      {action.busy&&<Busy/>}
      {tab==='quiz'&&<>
        <button className="btn primary" disabled={!valid||action.busy} onClick={quizStart}>Generate quiz</button>
        {quiz.map((card,index)=><div key={index} className="practice-item"><h3>{card.front}</h3><form onSubmit={e=>{e.preventDefault();void action.run(async()=>{const assessed=await api<Grade>(base+'/grade',{...input,question:card.front,answer:quizAnswers[index],marks:3});setResults({...results,[index]:assessed});setShown([...shown,String(index)])})}}>
          <label>Your answer<textarea required rows={2} value={quizAnswers[index]??''} onChange={e=>setQuizAnswers({...quizAnswers,[index]:e.target.value})}/></label><div className="actions"><button className="btn secondary" disabled={action.busy}>Check answer</button><button className="btn plain" type="button" onClick={()=>setShown([...shown,String(index)])}>Reveal</button></div></form>
          {shown.includes(String(index))&&<p className="notice">{card.back}</p>}{results[index]&&<GradeResult value={results[index]}/>}</div>)}
        <Sources sources={quizSources}/>
      </>}
      {tab==='flashcards'&&<>
        <button className="btn primary" disabled={!valid||action.busy} onClick={()=>void action.run(async()=>{await api(base+'/flashcards',input);await readCards()})}>Add topic flashcards</button>
        {!cards.length&&<Empty title="No flashcards yet">Choose a topic to create a review deck.</Empty>}
        {cards.map(card=><div className="practice-item" key={card.id}><div className="section-head"><h3>{card.front}</h3><span className="chip">{card.due<=localDay()?'Due today':'Due '+card.due}</span></div>
          {shown.includes(card.id)?<><p>{card.back}</p><div className="actions">{[false,true].map(recalled=><button key={String(recalled)} className="btn secondary" disabled={action.busy} onClick={()=>void action.run(async()=>{await api('/flashcards/'+card.id+'/review',{recalled});setShown(shown.filter(id=>id!==card.id));await readCards()})}>{recalled?'Remembered':'Review again tomorrow'}</button>)}</div></>:<button className="btn secondary" onClick={()=>setShown([...shown,card.id])}>Show answer</button>}</div>)}
      </>}
      {tab==='grade'&&<form onSubmit={e=>{e.preventDefault();void action.run(async()=>{setGrade(await api(base+'/grade',{...input,question,answer:text,marks,upload_id:photo?.id??null}));shared.clearProposal()})}}>
        <div className="field-grid"><label>Question<input required value={question} onChange={e=>setQuestion(e.target.value)}/></label><label>Marks<input required type="number" min="1" max="60" value={marks} onChange={e=>setMarks(Number(e.target.value))}/></label></div>
        <label>Answer or corrected transcription<textarea rows={6} value={text} onChange={e=>setText(e.target.value)}/></label>
        <label>Answer photograph<input type="file" accept="image/*" disabled={!base||action.busy} onChange={e=>{const file=e.target.files?.[0];if(!file)return;const data=new FormData();data.append('file',file);data.append('tag','other');void action.run(async()=>{const added=await api<Upload>(base+'/uploads',data);setPhoto(added);setText(added.pages.map(page=>page.text??'').join(String.fromCharCode(10)))})}}/></label>
        {photo?.pages.some(page=>page.review)&&<p className="notice">OCR needs review. Correct the transcription before grading.</p>}
        <button className="btn primary" disabled={!valid||!text.trim()||action.busy}>Grade answer</button>
        {grade&&<GradeResult value={grade}/>}
      </form>}
      {tab==='mock'&&<>
        <div className="section-head"><h2>Timed mock test</h2><button className="btn primary" disabled={!base||action.busy||!!(attempt&&!attempt.result&&remaining)} onClick={()=>void action.run(async()=>{setAttempt(await api(base+'/mock',{}));setClock(Date.now());setAnswers({});setAuto(false)})}>{attempt?'Start a new mock':'Start mock test'}</button></div>
        {attempt&&<><p className="notice"><Clock3 size={16}/> {attempt.paper.total} marks · {attempt.paper.minutes} minutes{!attempt.result?' · '+Math.floor(remaining/60)+':'+String(remaining%60).padStart(2,'0')+' remaining':''}</p>
          {!attempt.result?<form onSubmit={e=>{e.preventDefault();void action.run(()=>submit(attempt))}}>
            {attempt.paper.sections.map(section=><fieldset key={section.name}><legend>Part {section.name} · {section.marks} marks per question</legend>{section.groups.map(group=><div className="practice-item" key={group.module}><h3>{group.name} · Attempt {group.attempt} of {group.questions.length}</h3>{group.questions.map(q=><label key={q.id}>{q.id}. {q.text}<textarea rows={2} disabled={!remaining||action.busy||(!answers[q.id]?.trim()&&group.questions.filter(row=>answers[row.id]?.trim()).length>=group.attempt)} value={answers[q.id]??''} onChange={e=>changeAnswer(q.id,e.target.value)}/></label>)}</div>)}</fieldset>)}
            <button className="btn primary" disabled={action.busy}>Submit for grading</button>
          </form>:<div><h2>Result · {attempt.result.awarded}/{attempt.result.possible}</h2>{attempt.result.questions.map(item=><div className="practice-item" key={item.question.id}><h3>{item.question.text}</h3><GradeResult value={item}/></div>)}</div>}
        </>}
      </>}
    </section>
  </>
}
function GradeResult({value}:{value:Grade}){
  return <div className="notice"><strong>{value.assessment_available===false?'Grading unavailable':value.awarded+' / '+value.possible+' marks'}</strong><ul>{value.criteria.map((entry,i)=><li key={i}><strong>{entry.criterion} · {entry.awarded}/{entry.possible}</strong><p>{entry.feedback}</p></li>)}</ul><Sources sources={value.sources}/>{value.provider&&<small>{value.provider}</small>}</div>
}
