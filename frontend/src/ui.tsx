import {useEffect,useId,useRef,useState,type ReactNode} from 'react'
import {BookOpen, ExternalLink, LoaderCircle} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import {message,type Scope,type Source,type Subject,type Proposal} from './api'
import 'katex/dist/katex.min.css'

export type Shared = {subjects:Subject[],selected:number|null,scope?:Scope,proposal?:Proposal,clearProposal:()=>void}
export function useAction() {
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  async function run(task:()=>Promise<void>) {
    setError('');setBusy(true)
    try {await task()} catch(e) {setError(message(e))} finally {setBusy(false)}
  }
  return {busy,error,run,setError}
}
export function ErrorBox({text}:{text:string}) { return text ? <p className="error" role="alert">{text}</p> : null }
export function Busy({label='Working…'}:{label?:string}) {return <span className="busy" role="status"><LoaderCircle size={16}/>{label}</span>}
export function Empty({title,children,actions}:{title:string,children:ReactNode,actions?:ReactNode}) {
  return <div className="empty"><div className="empty-icon"><BookOpen size={23}/></div><h3>{title}</h3><p>{children}</p>{actions && <div className="actions">{actions}</div>}</div>
}
export function Heading({title,children,action}:{title:string,children:ReactNode,action?:ReactNode}) {
  return <header className="page-title"><div><h1>{title}</h1><p>{children}</p></div>{action}</header>
}
export function Sources({sources=[]}:{sources?:Source[]}) {
  if(!sources.length)return null
  return <div className="sources">{sources.map(source=><a key={source.label} href={source.url} target="_blank" rel="noreferrer">
    <span>{source.label}</span>{source.name}{source.page ? ' · p. '+source.page:''}<ExternalLink size={11}/></a>)}</div>
}
function Diagram({code}:{code:string}) {
  const id=useId().replace(/[^a-z0-9]/gi,'')
  const ref=useRef<HTMLDivElement>(null)
  const [failed,setFailed]=useState(false)
  useEffect(()=>{
    let cancelled=false
    setFailed(false)
    void import('mermaid').then(async ({default:mermaid})=>{
      if(cancelled)return
      mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'})
      const {svg}=await mermaid.render('study'+id,code)
      if(!cancelled&&ref.current)ref.current.innerHTML=svg
    }).catch(()=>{if(!cancelled)setFailed(true)})
    return ()=>{cancelled=true}
  },[code,id])
  return failed?<pre>{code}</pre>:<div className="diagram" ref={ref}/>
}
export function Markdown({value,sources=[]}:{value:string,sources?:Source[]}) {
  const byLabel=new Map(sources.map(source=>[source.label,source]))
  const content=value.replace(/\[(S\d+)\](?!\()/g,(match,id)=>byLabel.has(id)?match+'('+encodeURI(byLabel.get(id)!.url)+')':match)
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]}
    components={{a:({href,children})=><a href={href} target="_blank" rel="noreferrer">{children}</a>,
      img:({alt})=><span>{alt??'Image'}</span>,
      pre:({children})=><div className="code-block">{children}</div>,
      code:({className,children})=>className==='language-mermaid'?<Diagram code={String(children)}/>:<code className={className}>{children}</code>}}>{content}</ReactMarkdown></div>
}
export function ScopeFields({subjects,scope,onChange,allowAll=false,requireTopic=false}:{subjects:Subject[],scope:Scope,onChange:(s:Scope)=>void,allowAll?:boolean,requireTopic?:boolean}) {
  const subject=subjects.find(item=>item.id===scope.subject_id)
  const module=subject?.outline.modules.find(item=>item.id===scope.module)
  return <div className="field-grid scope-fields">
    <label>Subject<select value={scope.subject_id??''} onChange={e=>onChange({subject_id:Number(e.target.value)||null,module:null,topic:null})}>
      <option value="">{allowAll?'All subjects':'Select a subject'}</option>{subjects.map(item=><option value={item.id} key={item.id}>{item.code} · {item.name}</option>)}</select></label>
    <label>Module<select disabled={!subject} value={scope.module??''} onChange={e=>onChange({...scope,module:e.target.value||null,topic:null})}>
      <option value="">{requireTopic?'Select a module':'All modules'}</option>{subject?.outline.modules.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>Topic<select disabled={!module} value={scope.topic??''} onChange={e=>onChange({...scope,topic:e.target.value||null})}>
      <option value="">{requireTopic?'Select a topic':'All topics'}</option>{module?.topics.map(item=><option key={item}>{item}</option>)}</select></label>
  </div>
}
export function defaultScope(subjects:Subject[], selected?:number|null):Scope {
  const subject=subjects.find(item=>item.id===selected)??subjects[0]
  const module=subject?.outline.modules[0]
  return {subject_id:subject?.id??null,module:module?.id??null,topic:module?.topics[0]??null}
}
